import { useRef, useState } from 'react';

import { postOrchestratorChat } from '@/lib/orchestrator-chat-client';
import {
    dedupeConversationMessages,
    type OrchestratorConversationMessage,
} from '@/lib/orchestrator-chat-normalizer';

export type { OrchestratorConversationMessage } from '@/lib/orchestrator-chat-normalizer';

const ORCHESTRATOR_CHAT_ABORT_MS = 45_000;

export type CompanionMode = 'research' | 'project' | 'hybrid';
export type OrchestratorAgentKey = 'chat' | 'voice_chat' | 'reasoner' | 'coder';
export type RoutedTextFeatureKey = 'question' | 'research' | 'action';
export type ChatFunctionMode = 'auto' | RoutedTextFeatureKey;

export interface AdvisoryQuestion {
    prompt: string;
    reason?: string | null;
}

export interface BuildSuggestedSelfRunRequestOptions {
    action: AdvisoryNextAction;
    directiveTemplate: SuggestedSelfRunPreview['directiveTemplate'];
    baseRequest: string;
}

export interface AdvisoryEvidenceItem {
    title: string;
    source_label: string;
    source_type?: string;
    trust_score?: number;
    why_it_matters: string;
    url?: string | null;
}

export interface AdvisoryNextAction {
    title: string;
    action_type: string;
    detail: string;
    recommended_mode?: string | null;
}

export interface ProposalItem {
    title: string;
    category?: string;
    detail: string;
    benefit?: string | null;
    tradeoff?: string | null;
}

export interface TargetPatchHint {
    file_id: string;
    section_id?: string | null;
    feature_id?: string | null;
    chunk_id?: string | null;
    reason: string;
}

export interface SuggestedSelfRunPreview {
    action: AdvisoryNextAction;
    requestedMode: 'self-diagnosis' | 'self-improvement' | 'self-expansion';
    directiveTemplate:
        | ''
        | 'debug_remediation_loop'
        | 'video_ad_clarity'
        | 'video_ad_conversion'
        | 'video_ad_speed_optimization'
        | 'video_ad_storytelling'
        | 'video_ad_quality_upgrade'
        | 'video_ad_new_tech'
        | 'admin_ops_efficiency'
        | 'marketplace_conversion'
        | 'llm_cost_latency';
    directiveScope: 'preset_default' | 'diagnosis_only' | 'targeted_implementation' | 'feature_expansion' | 'modernization';
    directiveRequest: string;
}

export interface OrchestratorChatResponse {
    reply: OrchestratorConversationMessage;
    conversation?: OrchestratorConversationMessage[];
    output_dir?: string;
    failed_output_dir?: string;
    run_id?: string;
    grounding_mode?: string;
    grounding_note?: string | null;
    companion_mode?: string;
    web_results?: Array<{
        title: string;
        url?: string | null;
        snippet: string;
        domain?: string | null;
        source_type?: string;
        trust_score?: number;
    }>;
    suggested_companion_mode?: CompanionMode;
    suggested_companion_reason?: string | null;
    conversation_stage?: string;
    clarification_questions?: AdvisoryQuestion[];
    evidence_highlights?: AdvisoryEvidenceItem[];
    next_action_suggestions?: AdvisoryNextAction[];
    inferred_goal?: string | null;
    proposal_items?: ProposalItem[];
    new_technology_candidates?: string[];
    target_patch_hints?: TargetPatchHint[];
}

export interface VoiceResponse {
    transcript: string;
    response_text: string;
    audio_base64?: string;
    audio_format?: string;
    output_dir?: string;
    failed_output_dir?: string;
    run_id?: string;
    conversation?: OrchestratorConversationMessage[];
}

const ROUTED_TEXT_FEATURES: Array<{
    key: RoutedTextFeatureKey;
    lockedMode: CompanionMode;
}> = [
    { key: 'question', lockedMode: 'hybrid' },
    { key: 'research', lockedMode: 'research' },
    { key: 'action', lockedMode: 'project' },
];

const DEFAULT_ROUTED_TEXT_AGENTS: Record<RoutedTextFeatureKey, OrchestratorAgentKey> = {
    question: 'chat',
    research: 'reasoner',
    action: 'chat',
};

export const inferSuggestedSelfRunDirectiveTemplate = (
    action: AdvisoryNextAction,
    fallbackTemplate: SuggestedSelfRunPreview['directiveTemplate'],
    latestUserRequest: string,
): SuggestedSelfRunPreview['directiveTemplate'] => {
    if (fallbackTemplate) {
        return fallbackTemplate;
    }

    const signal = `${action.title} ${action.detail} ${latestUserRequest}`.toLowerCase();
    if (signal.includes('전환') || signal.includes('cta') || signal.includes('구매')) {
        return 'video_ad_conversion';
    }
    if (signal.includes('선명') || signal.includes('해상') || signal.includes('품질')) {
        return 'video_ad_clarity';
    }
    if (signal.includes('속도') || signal.includes('지연') || signal.includes('병목')) {
        return 'video_ad_speed_optimization';
    }
    if (signal.includes('신기술') || signal.includes('최신') || signal.includes('트렌드')) {
        return 'video_ad_new_tech';
    }
    if (signal.includes('스토리') || signal.includes('몰입')) {
        return 'video_ad_storytelling';
    }
    if (signal.includes('운영') || signal.includes('관리자')) {
        return 'admin_ops_efficiency';
    }
    if (signal.includes('마켓플레이스')) {
        return 'marketplace_conversion';
    }
    if (signal.includes('비용') || signal.includes('latency') || signal.includes('llm')) {
        return 'llm_cost_latency';
    }
    return '';
};

export const buildSuggestedSelfRunDirectiveRequest = ({
    action,
    baseRequest,
}: BuildSuggestedSelfRunRequestOptions) => {
    const advisoryLine = `${action.title}: ${action.detail}`;
    return baseRequest
        ? `${baseRequest}\n\n[협업 패널 제안]\n${advisoryLine}`
        : advisoryLine;
};

const detectRoutedTextFeature = (content: string): RoutedTextFeatureKey | null => {
    const normalized = content.trim().toLowerCase();
    if (!normalized) {
        return null;
    }
    const researchMarkers = ['정보수집', '정보 수집', '조사', '검색', '찾아', '수집', '자료', '근거', '공식문서', '트렌드', '최신'];
    if (researchMarkers.some((marker) => normalized.includes(marker))) {
        return 'research';
    }
    const actionMarkers = ['자가진단', '자가개선', '자가확장', '실행', '수정', '적용', '구현', '만들어', '고쳐', '바꿔', '처리해', '즉시', '바로', '프로그램', '앱', '서비스', '주식', '자동매매', '자동화'];
    if (actionMarkers.some((marker) => normalized.includes(marker))) {
        return 'action';
    }
    const questionMarkers = ['?', '왜', '뭐', '무엇', '어떻게', '어떤', '설명', '알려', '궁금', '차이'];
    if (normalized.endsWith('?') || questionMarkers.some((marker) => normalized.includes(marker))) {
        return 'question';
    }
    return null;
};

interface UseOrchestratorChatOptions {
    apiBaseUrl: string;
    adminFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
    getAdminToken: () => string;
    task: string;
    setTask: (value: string) => void;
    mode: string;
    manualMode: boolean;
    liveRunIdRef: React.MutableRefObject<string>;
    runtimeDraft?: { chat_request_max_tokens?: number; default_request_max_tokens?: number } | null;
    runtimeConfig?: { chat_request_max_tokens?: number } | null;
    workOutputDir: string;
    liveOutputDir: string;
    setWorkOutputDir: (value: string) => void;
    setLiveOutputDir: (value: string) => void;
    speakText?: (text: string) => void;
}

export function useOrchestratorChat(options: UseOrchestratorChatOptions) {
    const recognitionRef = useRef<any>(null);
    const [conversation, setConversation] = useState<OrchestratorConversationMessage[]>([
        {
            role: 'assistant',
            speaker: '오케스트레이터',
            step_title: '실행 안내',
            content: '관리자 화면은 챗봇 지시 중심으로 사용합니다. 필요한 수정이나 구현 내용을 바로 입력하면 실행 가능한 답과 변경 방향을 우선 정리합니다.',
        },
    ]);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const [voiceListening, setVoiceListening] = useState(false);
    const [chatAgentKey, setChatAgentKey] = useState<OrchestratorAgentKey>('chat');
    const [voiceAgentKey, setVoiceAgentKey] = useState<OrchestratorAgentKey>('reasoner');
    const [textFeatureAgents, setTextFeatureAgents] = useState<Record<RoutedTextFeatureKey, OrchestratorAgentKey>>(DEFAULT_ROUTED_TEXT_AGENTS);
    const [chatFunctionMode, setChatFunctionMode] = useState<ChatFunctionMode>('action');
    const [lastGroundingMode, setLastGroundingMode] = useState<'internal' | 'web'>('internal');
    const [lastGroundingNote, setLastGroundingNote] = useState('');
    const [companionMode, setCompanionMode] = useState<CompanionMode>('project');
    const [lastWebResults, setLastWebResults] = useState<NonNullable<OrchestratorChatResponse['web_results']>>([]);
    const [suggestedCompanionMode, setSuggestedCompanionMode] = useState<CompanionMode | null>(null);
    const [suggestedCompanionReason, setSuggestedCompanionReason] = useState('');
    const [lastConversationStage, setLastConversationStage] = useState('general');
    const [clarificationQuestions, setClarificationQuestions] = useState<AdvisoryQuestion[]>([]);
    const [evidenceHighlights, setEvidenceHighlights] = useState<AdvisoryEvidenceItem[]>([]);
    const [nextActionSuggestions, setNextActionSuggestions] = useState<AdvisoryNextAction[]>([]);
    const [inferredGoal, setInferredGoal] = useState('');
    const [proposalItems, setProposalItems] = useState<ProposalItem[]>([]);
    const [newTechnologyCandidates, setNewTechnologyCandidates] = useState<string[]>([]);
    const [targetPatchHints, setTargetPatchHints] = useState<TargetPatchHint[]>([]);
    const [conversationAssistExpanded, setConversationAssistExpanded] = useState(false);
    const [suggestedSelfRunPreview, setSuggestedSelfRunPreview] = useState<SuggestedSelfRunPreview | null>(null);

    const appendConversationMessage = (message: OrchestratorConversationMessage) => {
        setConversation((prev) => dedupeConversationMessages([...prev, message]));
    };

    const pushUserMessage = async () => {
        const content = chatInput.trim();
        setChatInput('');
        await sendChatMessage(content);
    };

    const playReturnedAudio = (audioBase64?: string, audioFormat?: string) => {
        if (!audioBase64 || !audioFormat || !audioFormat.startsWith('audio/')) {
            return false;
        }
        const audio = new Audio(`data:${audioFormat};base64,${audioBase64}`);
        void audio.play().catch(() => null);
        return true;
    };

    const pushVoiceMessage = async (transcript: string) => {
        if (!transcript.trim()) return;
        const userMessage: OrchestratorConversationMessage = {
            role: 'user',
            speaker: '관리자(음성)',
            content: transcript.trim(),
            timestamp: new Date().toISOString(),
        };
        const nextConversation = [...conversation, userMessage];
        setConversation(nextConversation);
        const nextTask = getEffectiveTaskInput() || transcript.trim();
        if (!options.task.trim()) {
            options.setTask(transcript.trim());
        }
        setChatLoading(true);
        try {
            const data = await postOrchestratorChat<VoiceResponse>(
                `${options.apiBaseUrl}/api/llm/voice/orchestrate`,
                options.getAdminToken(),
                {
                    transcript: transcript.trim(),
                    agent_key: voiceAgentKey,
                    tts: true,
                    auto_apply: false,
                    task: nextTask,
                    mode: options.manualMode ? 'manual_9step' : options.mode,
                    manual_mode: options.manualMode,
                    companion_mode: 'hybrid',
                    output_dir: resolveReusableOutputDir(),
                    run_id: options.liveRunIdRef.current || undefined,
                    max_tokens: getConversationRequestMaxTokens(),
                    conversation: nextConversation,
                },
            );
            if (Array.isArray(data.conversation) && data.conversation.length > 0) {
                setConversation(dedupeConversationMessages(data.conversation));
            } else {
                appendConversationMessage({
                    role: 'assistant',
                    speaker: '오케스트레이터',
                    step_title: '음성 응답',
                    content: data.response_text,
                    timestamp: new Date().toISOString(),
                });
            }
            if (data.output_dir) {
                options.setWorkOutputDir(data.output_dir);
            } else if (data.failed_output_dir) {
                options.setLiveOutputDir(data.failed_output_dir);
            }
            if (!playReturnedAudio(data.audio_base64, data.audio_format)) {
                options.speakText?.(data.response_text);
            }
        } catch (e: any) {
            appendConversationMessage({
                role: 'assistant',
                speaker: '오케스트레이터',
                step_title: '음성 오류',
                content: `음성 응답 실패: ${e.message}`,
                timestamp: new Date().toISOString(),
            });
        } finally {
            setChatLoading(false);
        }
    };

    const startVoiceInput = () => {
        if (voiceListening) {
            recognitionRef.current?.stop();
            return;
        }
        if (typeof window === 'undefined') {
            return;
        }
        const SpeechRecognitionCtor = (
            (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
        );
        if (!SpeechRecognitionCtor) {
            appendConversationMessage({
                role: 'assistant',
                speaker: '오케스트레이터',
                step_title: '음성 안내',
                content: '이 브라우저는 음성 인식을 지원하지 않습니다. 크롬 계열 브라우저에서 관리자 페이지를 열어 사용해 주세요.',
                timestamp: new Date().toISOString(),
            });
            return;
        }
        const recognition = new SpeechRecognitionCtor();
        recognition.lang = 'ko-KR';
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition.onresult = async (event: any) => {
            const transcript = String(event?.results?.[0]?.[0]?.transcript || '').trim();
            if (transcript) {
                await pushVoiceMessage(transcript);
            }
        };
        recognition.onerror = (event: any) => {
            const detail = String(event?.error || 'unknown');
            appendConversationMessage({
                role: 'assistant',
                speaker: '오케스트레이터',
                step_title: '음성 오류',
                content: `음성 인식 실패: ${detail}`,
                timestamp: new Date().toISOString(),
            });
        };
        recognition.onend = () => {
            setVoiceListening(false);
            recognitionRef.current = null;
        };
        recognitionRef.current = recognition;
        setVoiceListening(true);
        recognition.start();
    };

    const pushAssistantNotice = (stepTitle: string, content: string) => {
        appendConversationMessage({
            role: 'assistant',
            speaker: '오케스트레이터',
            step_title: stepTitle,
            content,
            timestamp: new Date().toISOString(),
        });
    };

    const setUnifiedPrompt = (value: string) => {
        options.setTask(value);
        setChatInput(value);
    };

    const appendUnifiedPrompt = (text: string) => {
        const normalized = text.trim();
        if (!normalized) {
            return;
        }
        const current = chatInput.trim();
        const nextValue = current ? `${current}\n${normalized}` : normalized;
        setUnifiedPrompt(nextValue);
    };

    const getEffectiveTaskInput = () => chatInput.trim() || options.task.trim();

    const getLatestUserConversationRequest = () => {
        const latestUserMessage = [...conversation]
            .reverse()
            .find((message) => message.role === 'user' && message.content.trim());
        return latestUserMessage?.content.trim() || getEffectiveTaskInput();
    };

    const getConversationRequestMaxTokens = () => {
        const configured = options.runtimeDraft?.chat_request_max_tokens
            ?? options.runtimeConfig?.chat_request_max_tokens
            ?? 768;
        return Math.max(128, Math.min(configured, options.runtimeDraft?.default_request_max_tokens ?? configured));
    };

    const resolveReusableOutputDir = () => {
        const candidate = String(options.workOutputDir || options.liveOutputDir || '').trim();
        if (!candidate) {
            return undefined;
        }
        const normalized = candidate.replace(/\\/g, '/').toLowerCase();
        if (!normalized.includes('/uploads/projects/')) {
            return undefined;
        }
        return candidate;
    };

    const sendChatMessage = async (content: string) => {
        if (!content) return;
        const routedFeature = chatFunctionMode !== 'auto'
            ? chatFunctionMode
            : detectRoutedTextFeature(content);
        const effectiveAgentKey = routedFeature ? textFeatureAgents[routedFeature] : chatAgentKey;
        const effectiveCompanionMode = routedFeature
            ? ROUTED_TEXT_FEATURES.find((feature) => feature.key === routedFeature)?.lockedMode || companionMode
            : companionMode;
        const userMessage: OrchestratorConversationMessage = {
            role: 'user',
            speaker: '관리자',
            content,
            timestamp: new Date().toISOString(),
        };
        const nextConversation = [...conversation, userMessage];
        setConversation(nextConversation);
        const nextTask = getEffectiveTaskInput() || content;
        if (!options.task.trim()) {
            options.setTask(content);
        }
        setChatInput('');
        setChatLoading(true);
        const controller = new AbortController();
        const abortTimer = window.setTimeout(() => controller.abort(), ORCHESTRATOR_CHAT_ABORT_MS);
        try {
            const data = await postOrchestratorChat<OrchestratorChatResponse>(
                `${options.apiBaseUrl}/api/llm/orchestrate/chat/light`,
                options.getAdminToken(),
                {
                    task: nextTask,
                    message: content,
                    agent_key: effectiveAgentKey,
                    mode: options.manualMode ? 'manual_9step' : options.mode,
                    manual_mode: options.manualMode,
                    companion_mode: effectiveCompanionMode,
                    output_dir: resolveReusableOutputDir(),
                    run_id: options.liveRunIdRef.current || undefined,
                    max_tokens: getConversationRequestMaxTokens(),
                    conversation: nextConversation,
                },
                controller.signal,
            );
            if (Array.isArray(data.conversation) && data.conversation.length > 0) {
                setConversation(dedupeConversationMessages(data.conversation));
            } else if (data.reply) {
                appendConversationMessage(data.reply);
            }
            setLastGroundingMode(data.grounding_mode === 'web' ? 'web' : 'internal');
            setLastGroundingNote(data.grounding_note || '');
            setLastWebResults(Array.isArray(data.web_results) ? data.web_results : []);
            setSuggestedCompanionMode(data.suggested_companion_mode || null);
            setSuggestedCompanionReason(data.suggested_companion_reason || '');
            setLastConversationStage(data.conversation_stage || 'general');
            setClarificationQuestions(Array.isArray(data.clarification_questions) ? data.clarification_questions : []);
            setEvidenceHighlights(Array.isArray(data.evidence_highlights) ? data.evidence_highlights : []);
            setNextActionSuggestions(Array.isArray(data.next_action_suggestions) ? data.next_action_suggestions : []);
            setInferredGoal(data.inferred_goal || '');
            setProposalItems(Array.isArray(data.proposal_items) ? data.proposal_items : []);
            setNewTechnologyCandidates(Array.isArray(data.new_technology_candidates) ? data.new_technology_candidates : []);
            setTargetPatchHints(Array.isArray(data.target_patch_hints) ? data.target_patch_hints : []);
            if (data.output_dir) {
                options.setWorkOutputDir(data.output_dir);
            } else if (data.failed_output_dir) {
                options.setLiveOutputDir(data.failed_output_dir);
            }
        } catch (e: any) {
            const timeoutMessage = `오케스트레이터 응답이 ${Math.round(ORCHESTRATOR_CHAT_ABORT_MS / 1000)}초 안에 끝나지 않아 요청을 중단했습니다. 질문 범위를 조금 줄여 다시 시도해 주세요.`;
            appendConversationMessage({
                role: 'assistant',
                speaker: '오케스트레이터',
                step_title: '대화 오류',
                content: e?.name === 'AbortError' ? timeoutMessage : `대화 응답 실패: ${e.message}`,
                timestamp: new Date().toISOString(),
            });
        } finally {
            window.clearTimeout(abortTimer);
            setChatLoading(false);
        }
    };

    return {
        conversation,
        chatInput,
        chatLoading,
        voiceListening,
        chatAgentKey,
        voiceAgentKey,
        textFeatureAgents,
        chatFunctionMode,
        lastGroundingMode,
        lastGroundingNote,
        companionMode,
        lastWebResults,
        suggestedCompanionMode,
        suggestedCompanionReason,
        lastConversationStage,
        clarificationQuestions,
        evidenceHighlights,
        nextActionSuggestions,
        inferredGoal,
        proposalItems,
        newTechnologyCandidates,
        targetPatchHints,
        conversationAssistExpanded,
        suggestedSelfRunPreview,
        recognitionRef,
        setConversation,
        setChatInput,
        setVoiceListening,
        setChatAgentKey,
        setVoiceAgentKey,
        setTextFeatureAgents,
        setChatFunctionMode,
        setCompanionMode,
        setConversationAssistExpanded,
        setSuggestedSelfRunPreview,
        appendConversationMessage,
        pushAssistantNotice,
        setUnifiedPrompt,
        appendUnifiedPrompt,
        getEffectiveTaskInput,
        getLatestUserConversationRequest,
        resolveReusableOutputDir,
        sendChatMessage,
        pushUserMessage,
        pushVoiceMessage,
        startVoiceInput,
    };
}
