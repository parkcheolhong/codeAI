#!/usr/bin/env python3
"""
GPU 기반 LLM 추론 서버
양자화 지원: 4bit, 8bit, GPTQ, AWQ
"""

import os
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    TextIteratorStreamer
)
from threading import Thread
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI 앱
app = FastAPI(title="GPU LLM Server", version="1.0.0")

# CORS 설정 — 환경변수 ALLOWED_ORIGINS로 허용 origin 제어 (쉼표 구분)
# 기본값: 로컬 개발 환경 (운영 배포 시 .env에서 재정의)
_raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://host.docker.internal:8000,http://localhost:8000,http://127.0.0.1:8000,"
    "http://localhost:3005,http://localhost:3000,http://127.0.0.1:3005,http://127.0.0.1:3000,"
    "https://metanova1004.com:3000,https://metanova1004.com,https://개발분석114.com:3005,https://개발분석114.com,"
    "https://xn--114-2p7l635dz3bh5j.com:3005,https://xn--114-2p7l635dz3bh5j.com",
)
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# 전역 변수
model = None
tokenizer = None
model_load_error = None
device = "cuda" if torch.cuda.is_available() else "cpu"

# 환경 변수에서 설정 로드
MODEL_NAME = os.getenv("MODEL_NAME", "meta-llama/Llama-2-7b-chat-hf")
LOAD_IN_4BIT = os.getenv("LOAD_IN_4BIT", "true").lower() == "true"
LOAD_IN_8BIT = os.getenv("LOAD_IN_8BIT", "false").lower() == "true"
USE_FLASH_ATTENTION = os.getenv("USE_FLASH_ATTENTION", "true").lower() == "true"
MAX_NEW_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "512"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))


class ChatRequest(BaseModel):
    prompt: str
    max_tokens: Optional[int] = MAX_NEW_TOKENS
    temperature: Optional[float] = TEMPERATURE
    top_p: Optional[float] = 0.9
    top_k: Optional[int] = 50
    stream: Optional[bool] = False
    system_prompt: Optional[str] = "You are a helpful AI assistant."


class ChatResponse(BaseModel):
    response: str
    model: str
    tokens_generated: int


def load_model():
    """모델 로드 with 양자화 옵션"""
    global model, tokenizer, model_load_error
    
    logger.info(f"Loading model: {MODEL_NAME}")
    logger.info(f"Device: {device}")
    logger.info(f"4bit quantization: {LOAD_IN_4BIT}")
    logger.info(f"8bit quantization: {LOAD_IN_8BIT}")
    
    # 토크나이저 로드
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    is_awq_or_gptq = ("awq" in MODEL_NAME.lower()) or ("gptq" in MODEL_NAME.lower())

    # AWQ 전용 로더 경로 우선 시도 (generic 로더의 meta tensor 실패 우회)
    if ("awq" in MODEL_NAME.lower()) and not (LOAD_IN_4BIT or LOAD_IN_8BIT):
        try:
            from awq import AutoAWQForCausalLM

            awq_kwargs = {
                "fuse_layers": False,
                "safetensors": True,
            }
            if torch.cuda.is_available():
                awq_kwargs["device_map"] = {"": 0}

            logger.info("Trying AWQ dedicated loader path")
            model = AutoAWQForCausalLM.from_quantized(MODEL_NAME, **awq_kwargs)
            logger.info("Model loaded successfully via AWQ loader!")

            if torch.cuda.is_available():
                logger.info(f"GPU Memory allocated: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
                logger.info(f"GPU Memory reserved: {torch.cuda.memory_reserved() / 1e9:.2f} GB")
            return
        except Exception as awq_error:
            logger.warning(f"AWQ dedicated loader failed, fallback to generic loader: {awq_error}")

    # 양자화 설정
    quantization_config = None
    if LOAD_IN_4BIT:
        logger.info("Using 4-bit quantization (BitsAndBytes)")
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4"
        )
    elif LOAD_IN_8BIT:
        logger.info("Using 8-bit quantization (BitsAndBytes)")
        quantization_config = BitsAndBytesConfig(
            load_in_8bit=True,
            llm_int8_threshold=6.0
        )
    
    # 모델 로드 설정
    model_kwargs = {
        "device_map": "auto",
        "torch_dtype": torch.bfloat16 if not (LOAD_IN_4BIT or LOAD_IN_8BIT) else torch.float16,
        "low_cpu_mem_usage": True,
    }

    # AWQ/GPTQ는 일부 조합에서 meta tensor 경로(device_map=auto, low_cpu_mem_usage=True)로
    # 로드 시 실패할 수 있어 단일 GPU 강제 로딩으로 우회한다.
    if is_awq_or_gptq and torch.cuda.is_available() and not (LOAD_IN_4BIT or LOAD_IN_8BIT):
        model_kwargs["device_map"] = {"": 0}
        model_kwargs["low_cpu_mem_usage"] = False
        model_kwargs["torch_dtype"] = torch.float16
        logger.info("Using single-GPU load path for AWQ/GPTQ to avoid meta tensor load issues")
    
    if quantization_config:
        model_kwargs["quantization_config"] = quantization_config
    
    if USE_FLASH_ATTENTION:
        try:
            model_kwargs["attn_implementation"] = "flash_attention_2"
            logger.info("Using Flash Attention 2")
        except Exception as e:
            logger.warning(f"Flash Attention not available: {e}")
    
    # 모델 로드
    try:
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            **model_kwargs
        )
        logger.info("Model loaded successfully!")
        
        # GPU 메모리 정보
        if torch.cuda.is_available():
            logger.info(f"GPU Memory allocated: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
            logger.info(f"GPU Memory reserved: {torch.cuda.memory_reserved() / 1e9:.2f} GB")
    
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        model = None
        tokenizer = None
        model_load_error = str(e)
        logger.warning("Server will stay up without a loaded model.")


def generate_response(prompt: str, **kwargs):
    """텍스트 생성"""
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    
    generation_config = {
        "max_new_tokens": kwargs.get("max_tokens", MAX_NEW_TOKENS),
        "temperature": kwargs.get("temperature", TEMPERATURE),
        "top_p": kwargs.get("top_p", 0.9),
        "top_k": kwargs.get("top_k", 50),
        "do_sample": True,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
    }
    
    with torch.no_grad():
        outputs = model.generate(**inputs, **generation_config)
    
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    
    # 프롬프트 제거
    if response.startswith(prompt):
        response = response[len(prompt):].strip()
    
    return response


def generate_stream(prompt: str, **kwargs):
    """스트리밍 생성"""
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    
    streamer = TextIteratorStreamer(
        tokenizer,
        skip_prompt=True,
        skip_special_tokens=True
    )
    
    generation_config = {
        "max_new_tokens": kwargs.get("max_tokens", MAX_NEW_TOKENS),
        "temperature": kwargs.get("temperature", TEMPERATURE),
        "top_p": kwargs.get("top_p", 0.9),
        "top_k": kwargs.get("top_k", 50),
        "do_sample": True,
        "streamer": streamer,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": tokenizer.eos_token_id,
    }
    
    # 별도 스레드에서 생성
    thread = Thread(target=model.generate, kwargs={**inputs, **generation_config})
    thread.start()
    
    # 스트리밍 응답
    for text in streamer:
        yield text


@app.on_event("startup")
async def startup_event():
    """서버 시작 시 모델 로드"""
    load_model()


@app.get("/")
async def root():
    """헬스체크"""
    return {
        "status": "running",
        "model": MODEL_NAME,
        "device": device,
        "quantization": "4bit" if LOAD_IN_4BIT else "8bit" if LOAD_IN_8BIT else "none",
        "cuda_available": torch.cuda.is_available(),
        "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0
    }


@app.get("/health")
async def health():
    """상세 헬스체크"""
    health_info = {
        "status": "healthy" if model is not None else "degraded",
        "model_loaded": model is not None,
        "tokenizer_loaded": tokenizer is not None,
        "model_load_error": model_load_error,
    }
    
    if torch.cuda.is_available():
        health_info["gpu"] = {
            "device_name": torch.cuda.get_device_name(0),
            "memory_allocated_gb": torch.cuda.memory_allocated() / 1e9,
            "memory_reserved_gb": torch.cuda.memory_reserved() / 1e9,
            "memory_total_gb": torch.cuda.get_device_properties(0).total_memory / 1e9
        }
    
    return health_info


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """채팅 엔드포인트"""
    if model is None or tokenizer is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    # 프롬프트 구성
    full_prompt = f"{request.system_prompt}\n\nUser: {request.prompt}\n\nAssistant:"
    
    # 스트리밍 모드
    if request.stream:
        async def stream_generator():
            for chunk in generate_stream(
                full_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k
            ):
                yield chunk
        
        return StreamingResponse(stream_generator(), media_type="text/plain")
    
    # 일반 모드
    response_text = generate_response(
        full_prompt,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        top_p=request.top_p,
        top_k=request.top_k
    )
    
    return ChatResponse(
        response=response_text,
        model=MODEL_NAME,
        tokens_generated=len(tokenizer.encode(response_text))
    )


@app.post("/generate")
async def generate(request: ChatRequest):
    """간단한 생성 엔드포인트"""
    if model is None or tokenizer is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    if request.stream:
        async def stream_generator():
            for chunk in generate_stream(
                request.prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature
            ):
                yield chunk
        
        return StreamingResponse(stream_generator(), media_type="text/plain")
    
    response_text = generate_response(
        request.prompt,
        max_tokens=request.max_tokens,
        temperature=request.temperature
    )
    
    return {"text": response_text}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
