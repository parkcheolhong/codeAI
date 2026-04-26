import gradio as gr
import requests
import os
import json

API_URL = os.getenv("API_URL", "http://localhost:8000")

def chat_with_llm(message, history, system_prompt, temperature, max_tokens, stream):
    """LLM과 채팅"""
    
    # API 요청
    payload = {
        "prompt": message,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream
    }
    
    try:
        if stream:
            # 스트리밍 모드
            response = requests.post(
                f"{API_URL}/chat",
                json=payload,
                stream=True
            )
            
            full_response = ""
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    full_response += chunk
                    yield full_response
        else:
            # 일반 모드
            response = requests.post(
                f"{API_URL}/chat",
                json=payload
            )
            
            if response.status_code == 200:
                result = response.json()
                yield result.get("response", "No response")
            else:
                yield f"Error: {response.status_code} - {response.text}"
    
    except Exception as e:
        yield f"Error: {str(e)}"


def get_server_status():
    """서버 상태 확인"""
    try:
        response = requests.get(f"{API_URL}/health")
        if response.status_code == 200:
            data = response.json()
            
            status_text = f"""
## 🟢 Server Status: Healthy

**Model**: {data.get('model_loaded', 'Unknown')}
**Tokenizer**: {data.get('tokenizer_loaded', 'Unknown')}

"""
            if 'gpu' in data:
                gpu_info = data['gpu']
                status_text += f"""
### GPU Information
- **Device**: {gpu_info.get('device_name', 'Unknown')}
- **Memory Allocated**: {gpu_info.get('memory_allocated_gb', 0):.2f} GB
- **Memory Reserved**: {gpu_info.get('memory_reserved_gb', 0):.2f} GB
- **Total Memory**: {gpu_info.get('memory_total_gb', 0):.2f} GB
"""
            return status_text
        else:
            return f"## 🔴 Server Error: {response.status_code}"
    except Exception as e:
        return f"## 🔴 Server Offline\n\nError: {str(e)}"


# Gradio 인터페이스
with gr.Blocks(title="GPU LLM Server", theme=gr.themes.Soft()) as demo:
    
    gr.Markdown("""
    # 🚀 GPU LLM Server
    
    양자화 지원 고성능 LLM 추론 서버
    
    **Features:**
    - 🎯 4bit/8bit 양자화
    - ⚡ Flash Attention 2
    - 🔄 스트리밍 응답
    - 🎨 커스터마이징 가능한 파라미터
    """)
    
    with gr.Tab("💬 Chat"):
        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    [],
                    elem_id="chatbot",
                    bubble_full_width=False,
                    height=500
                )
                
                msg = gr.Textbox(
                    label="Your Message",
                    placeholder="Type your message here...",
                    lines=3
                )
                
                with gr.Row():
                    submit_btn = gr.Button("Send", variant="primary")
                    clear_btn = gr.Button("Clear")
            
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ Settings")
                
                system_prompt = gr.Textbox(
                    label="System Prompt",
                    value="You are a helpful AI assistant.",
                    lines=3
                )
                
                temperature = gr.Slider(
                    minimum=0.1,
                    maximum=2.0,
                    value=0.7,
                    step=0.1,
                    label="Temperature"
                )
                
                max_tokens = gr.Slider(
                    minimum=50,
                    maximum=2048,
                    value=512,
                    step=50,
                    label="Max Tokens"
                )
                
                stream = gr.Checkbox(
                    label="Enable Streaming",
                    value=True
                )
        
        def user(user_message, history):
            return "", history + [[user_message, None]]
        
        def bot(history, system_prompt, temperature, max_tokens, stream):
            user_message = history[-1][0]
            
            bot_message = ""
            for response in chat_with_llm(
                user_message,
                history,
                system_prompt,
                temperature,
                max_tokens,
                stream
            ):
                bot_message = response
                history[-1][1] = bot_message
                yield history
        
        msg.submit(user, [msg, chatbot], [msg, chatbot], queue=False).then(
            bot, [chatbot, system_prompt, temperature, max_tokens, stream], chatbot
        )
        submit_btn.click(user, [msg, chatbot], [msg, chatbot], queue=False).then(
            bot, [chatbot, system_prompt, temperature, max_tokens, stream], chatbot
        )
        clear_btn.click(lambda: None, None, chatbot, queue=False)
    
    with gr.Tab("📊 Server Status"):
        status_output = gr.Markdown()
        refresh_btn = gr.Button("Refresh Status", variant="primary")
        
        refresh_btn.click(get_server_status, None, status_output)
        demo.load(get_server_status, None, status_output)
    
    with gr.Tab("📖 API Documentation"):
        gr.Markdown("""
        ## API Endpoints
        
        ### POST /chat
        채팅 인터페이스
        
        **Request:**
        ```json
        {
            "prompt": "Hello, how are you?",
            "system_prompt": "You are a helpful assistant.",
            "temperature": 0.7,
            "max_tokens": 512,
            "top_p": 0.9,
            "top_k": 50,
            "stream": false
        }
        ```
        
        **Response:**
        ```json
        {
            "response": "I'm doing well, thank you!",
            "model": "meta-llama/Llama-2-7b-chat-hf",
            "tokens_generated": 10
        }
        ```
        
        ### POST /generate
        간단한 텍스트 생성
        
        **Request:**
        ```json
        {
            "prompt": "Once upon a time",
            "max_tokens": 100,
            "temperature": 0.8
        }
        ```
        
        ### GET /health
        서버 상태 확인
        
        ### cURL Examples
        
        ```bash
        # 채팅 요청
        curl -X POST http://localhost:8000/chat \\
          -H "Content-Type: application/json" \\
          -d '{
            "prompt": "What is AI?",
            "temperature": 0.7,
            "max_tokens": 200
          }'
        
        # 스트리밍 요청
        curl -X POST http://localhost:8000/chat \\
          -H "Content-Type: application/json" \\
          -d '{
            "prompt": "Tell me a story",
            "stream": true
          }' \\
          --no-buffer
        
        # 서버 상태
        curl http://localhost:8000/health
        ```
        """)

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )
