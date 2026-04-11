'use client';

import * as React from 'react';
import { resolveApiBaseUrl } from '@shared/api';
import OrchestratorStageCardPanel, { type SharedOrchestratorStageRun } from '@shared/orchestrator-stage-card-panel';
import SharedOrchestratorFollowUpCard from '@shared/orchestrator-follow-up-card';
import { buildFollowUpPriorityScore } from '@shared/orchestrator-follow-up-history';
import {
    MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY,
    type MarketplaceOrchestratorBridgePayload,
} from '@/lib/admin-orchestrator-bridge';

type ConversationMessage = {
    role: string;
    content: string;
    speaker?: string | null;
    timestamp?: string | null;
    step_title?: string | null;
};

type Product = {
    id: string;
    title: string;
    category: string;
    price: string;
    summary: string;
    highlights: string[];
};

type CustomerOrchestrateResult = {
    requested_by?: { id: number; email: string };
    result?: {
        final_output?: string;
        output_dir?: string | null;
        completion_summary?: string | null;
        failure_summary?: string | null;
        apply_error?: string | null;
        postcheck_error?: string | null;
        stage_run?: SharedOrchestratorStageRun;
    };
};

type CompletionItem = {
    id: number;
    project_name: string;
    mode: string;
    attempts: number;
    output_dir?: string | null;
    gate_passed: boolean;
    created_at?: string;
};

type FeatureLogItem = {
    id: number;
    status: string;
    message: string;
    flow_id?: string | null;
    step_id?: string | null;
    action?: string | null;
    created_at?: string;
};

type RetryQueueItem = {
    id: number;
    queue_name: string;
    status: string;
    last_error?: string | null;
    attempt_count?: number;
    updated_at?: string;
};

type RetryReplayResponse = {
    id?: number;
    status?: string;
    attempt_count?: number;
    last_error?: string | null;
};

type GeneratedProgramSummary = {
    output_dir?: string | null;
    output_archive_path?: string | null;
    delivery_gate_blocked: boolean;
    delivery_gate_message?: string | null;
    publish_ready: boolean;
    publish_targets: string[];
    shipping_zip_ok: boolean;
    validation_profile?: string | null;
    required_tests: string[];
    priority_average_score?: number;
    priority_peak_score?: number;
    priority_latest_score?: number;
    priority_previous_score?: number | null;
    priority_momentum?: number;
    priority_cumulative_score?: number;
    approval_history_count?: number;
    stage_run_status?: string | null;
    hard_gate_failed_stages?: string[];
};

type Props = {
    selectedProduct: Product;
    initialProjectName: string;
    initialTaskDraft: string;
    sourceProjectTitle?: string | null;
};

const CUSTOMER_TOKEN_KEY = 'customer_token';

type CustomerMemberType = 'individual' | 'sole_proprietor' | 'corporation';

const MEMBER_TYPE_LABELS: Record<CustomerMemberType, string> = {
    individual: '개인',
    sole_proprietor: '개인사업자',
    corporation: '법인사업자',
};

function buildTask(product: Product, userPrompt: string, projectName: string) {
    return [
        `[상품 주문 오케스트레이터]`,
        `- 상품 ID: ${product.id}`,
        `- 상품명: ${product.title}`,
        `- 카테고리: ${product.category}`,
        `- 가격: ${product.price}`,
        `- 프로젝트명: ${projectName}`,
        `- 핵심 포인트: ${product.highlights.join(', ')}`,
        '',
        '[고객 요청]',
        userPrompt.trim(),
    ].join('\n');
}

export default function MarketplaceOrchestratorClient({
    selectedProduct,
    initialProjectName,
    initialTaskDraft,
    sourceProjectTitle,
}: Props) {
    const apiBaseUrl = React.useMemo(() => resolveApiBaseUrl(), []);
    const [token, setToken] = React.useState('');
    const [me, setMe] = React.useState<{ email: string; username: string; full_name?: string | null; member_type?: string; business_name?: string | null; business_registration_number?: string | null; representative_name?: string | null } | null>(null);
    const [authMode, setAuthMode] = React.useState<'login' | 'signup'>('login');
    const [email, setEmail] = React.useState('');
    const [username, setUsername] = React.useState('');
    const [fullName, setFullName] = React.useState('');
    const [memberType, setMemberType] = React.useState<CustomerMemberType>('individual');
    const [businessName, setBusinessName] = React.useState('');
    const [businessRegistrationNumber, setBusinessRegistrationNumber] = React.useState('');
    const [representativeName, setRepresentativeName] = React.useState('');
    const [password, setPassword] = React.useState('');
    const [authLoading, setAuthLoading] = React.useState(false);
    const [authMessage, setAuthMessage] = React.useState('');
    const [projectName, setProjectName] = React.useState(initialProjectName);
    const [taskDraft, setTaskDraft] = React.useState(initialTaskDraft);
    const [runId, setRunId] = React.useState('');
    const [stageRun, setStageRun] = React.useState<SharedOrchestratorStageRun | null>(null);
    const [stageNoteDraft, setStageNoteDraft] = React.useState('');
    const [stageRevisionNote, setStageRevisionNote] = React.useState('');
    const [stageSubstepChecks, setStageSubstepChecks] = React.useState<Record<string, boolean>>({});
    const [stageLoading, setStageLoading] = React.useState(false);
    const [submitLoading, setSubmitLoading] = React.useState(false);
    const [resultText, setResultText] = React.useState('');
    const [errorText, setErrorText] = React.useState('');
    const [logs, setLogs] = React.useState<FeatureLogItem[]>([]);
    const [completions, setCompletions] = React.useState<CompletionItem[]>([]);
    const [retryQueue, setRetryQueue] = React.useState<RetryQueueItem[]>([]);
    const [generatedProgramSummary, setGeneratedProgramSummary] = React.useState<GeneratedProgramSummary | null>(null);
    const [conversation, setConversation] = React.useState<ConversationMessage[]>([]);
    const [chatInput, setChatInput] = React.useState('');
    const [chatLoading, setChatLoading] = React.useState(false);

    const authHeaders = React.useMemo(() => (
        token ? { Authorization: `Bearer ${token}` } : undefined
    ), [token]);

    const activeStage = React.useMemo(
        () => (stageRun?.stages || []).find((stage) => stage.id === stageRun?.current_stage_id) || null,
        [stageRun],
    );
    const customerFollowUpScore = React.useMemo(() => {
        const completionPenalty = generatedProgramSummary?.publish_ready ? 5 : 75;
        const gatePenalty = generatedProgramSummary?.delivery_gate_blocked ? 85 : 10;
        const retryPenalty = Math.min(100, retryQueue.length * 20);
        const activePenalty = activeStage?.status === 'failed' ? 90 : activeStage?.status === 'manual_correction' ? 60 : 15;
        return buildFollowUpPriorityScore({
            severity: completionPenalty,
            recency: activePenalty,
            approvalRisk: Math.min(100, (generatedProgramSummary?.approval_history_count ?? 0) * 25),
            hardGateImpact: gatePenalty,
            operationalRisk: retryPenalty,
            selfRunPriority: (generatedProgramSummary?.stage_run_status === 'failed' || generatedProgramSummary?.stage_run_status === 'manual_correction') ? 80 : 20,
        });
    }, [activeStage?.status, generatedProgramSummary?.delivery_gate_blocked, generatedProgramSummary?.publish_ready, retryQueue.length]);
    const customerFollowUpRecommendations = React.useMemo(() => {
        const items: Array<{ id: string; label: string; detail: string }> = [];
        if (generatedProgramSummary?.delivery_gate_message) {
            items.push({ id: 'delivery-gate', label: '출고 게이트 보정', detail: generatedProgramSummary.delivery_gate_message });
        }
        if (activeStage) {
            items.push({ id: 'active-stage', label: '현재 카드 우선 처리', detail: `${activeStage.label} · ${activeStage.title} 상태=${activeStage.status}` });
        }
        if (retryQueue.length > 0) {
            items.push({ id: 'retry-queue', label: '재시도 큐 정리', detail: `재시도 대기 ${retryQueue.length}건을 먼저 정리하세요.` });
        }
        if (generatedProgramSummary?.required_tests?.length) {
            items.push({ id: 'required-tests', label: '필수 검증 유지', detail: `required tests: ${generatedProgramSummary.required_tests.join(', ')}` });
        }
        return items.slice(0, 4);
    }, [activeStage, generatedProgramSummary?.delivery_gate_message, generatedProgramSummary?.required_tests, retryQueue.length]);
    const customerHistoryStats = {
        averageScore: generatedProgramSummary?.priority_average_score ?? customerFollowUpScore.weighted,
        peakScore: generatedProgramSummary?.priority_peak_score ?? customerFollowUpScore.weighted,
        latestScore: generatedProgramSummary?.priority_latest_score ?? customerFollowUpScore.weighted,
        previousScore: generatedProgramSummary?.priority_previous_score ?? null,
        momentum: generatedProgramSummary?.priority_momentum ?? 0,
        cumulativeScore: generatedProgramSummary?.priority_cumulative_score ?? customerFollowUpScore.weighted,
    };

    const refreshStageRun = React.useCallback(async (targetRunId?: string) => {
        const effectiveRunId = targetRunId || runId;
        if (!effectiveRunId || !authHeaders) return;
        const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/stage-runs/${encodeURIComponent(effectiveRunId)}`, {
            headers: authHeaders,
            cache: 'no-store',
        });
        if (!response.ok) {
            return;
        }
        const payload = await response.json();
        setStageRun(payload);
    }, [apiBaseUrl, authHeaders, runId]);

    const loadMyInfo = React.useCallback(async (targetToken: string) => {
        const response = await fetch(`${apiBaseUrl}/api/auth/me`, {
            headers: { Authorization: `Bearer ${targetToken}` },
            cache: 'no-store',
        });
        if (!response.ok) {
            throw new Error('내 정보를 불러오지 못했습니다.');
        }
        const payload = await response.json();
        setMe(payload);
    }, [apiBaseUrl]);

    const loadHistory = React.useCallback(async (targetToken?: string) => {
        const effectiveToken = targetToken || token;
        if (!effectiveToken) return;
        const headers = { Authorization: `Bearer ${effectiveToken}` };
        const [completionResponse, logResponse, retryResponse] = await Promise.all([
            fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/completions/my`, { headers, cache: 'no-store' }),
            fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/logs/my`, { headers, cache: 'no-store' }),
            fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/retry-queue/my`, { headers, cache: 'no-store' }),
        ]);
        if (completionResponse.ok) {
            setCompletions(await completionResponse.json());
        }
        if (logResponse.ok) {
            setLogs(await logResponse.json());
        }
        if (retryResponse.ok) {
            setRetryQueue(await retryResponse.json());
        }
        const generatedProgramResponse = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/generated-programs/latest`, { headers, cache: 'no-store' });
        if (generatedProgramResponse.ok) {
            setGeneratedProgramSummary(await generatedProgramResponse.json());
        }
    }, [apiBaseUrl, token]);

    const replayRetryQueueItem = React.useCallback(async (queueItemId: number) => {
        if (!authHeaders) {
            setErrorText('로그인 후 재시도 큐를 다시 실행할 수 있습니다.');
            return;
        }
        setErrorText('');
        setResultText('');
        try {
            const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/retry-queue/my/${queueItemId}/replay`, {
                method: 'POST',
                headers: authHeaders,
            });
            const payload = await response.json().catch(() => null) as RetryReplayResponse | null;
            if (!response.ok) {
                throw new Error((payload as { detail?: string } | null)?.detail || '재시도 큐 재실행에 실패했습니다.');
            }
            setResultText(`재시도 큐 ${queueItemId}번을 다시 실행했습니다.${payload?.attempt_count != null ? ` (시도 ${payload.attempt_count})` : ''}`);
            await loadHistory();
        } catch (error: any) {
            setErrorText(error?.message || '재시도 큐 재실행 중 오류가 발생했습니다.');
        }
    }, [apiBaseUrl, authHeaders, loadHistory]);

    React.useEffect(() => {
        if (typeof window === 'undefined') return;
        const savedToken = localStorage.getItem(CUSTOMER_TOKEN_KEY) || '';
        if (!savedToken) return;
        setToken(savedToken);
        loadMyInfo(savedToken).catch(() => {
            localStorage.removeItem(CUSTOMER_TOKEN_KEY);
            setToken('');
            setMe(null);
        });
        loadHistory(savedToken).catch(() => {});
    }, [loadHistory, loadMyInfo]);

    React.useEffect(() => {
        if (typeof window === 'undefined') return;
        try {
            const raw = localStorage.getItem(MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY);
            if (!raw) return;
            const payload = JSON.parse(raw) as MarketplaceOrchestratorBridgePayload;
            if (payload?.source === 'admin-llm') {
                if (payload.productId && payload.productId === selectedProduct.id) {
                    setProjectName(payload.projectName || initialProjectName);
                    setTaskDraft(payload.task || initialTaskDraft);
                }
            }
            localStorage.removeItem(MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY);
        } catch {
        }
    }, [initialProjectName, initialTaskDraft, selectedProduct.id]);

    React.useEffect(() => {
        const checks = (activeStage?.substeps || []).reduce<Record<string, boolean>>((acc, item) => {
            acc[item.id] = Boolean(item.checked);
            return acc;
        }, {});
        setStageSubstepChecks(checks);
    }, [activeStage?.id]);

    const handleAuth = React.useCallback(async () => {
        setAuthLoading(true);
        setAuthMessage('');
        try {
            if (authMode === 'signup') {
                const signupResponse = await fetch(`${apiBaseUrl}/api/auth/signup`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: username.trim(),
                        email: email.trim(),
                        password,
                        full_name: fullName.trim(),
                        member_type: memberType,
                        business_name: memberType === 'individual' ? null : businessName.trim(),
                        business_registration_number: memberType === 'individual' ? null : businessRegistrationNumber.trim(),
                        representative_name: memberType === 'corporation' ? representativeName.trim() : null,
                    }),
                });
                const signupPayload = await signupResponse.json().catch(() => null);
                if (!signupResponse.ok) {
                    throw new Error(signupPayload?.detail || '회원가입에 실패했습니다.');
                }
            }

            const formData = new URLSearchParams();
            formData.set('username', email.trim());
            formData.set('password', password);

            const loginResponse = await fetch(`${apiBaseUrl}/api/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString(),
            });
            const loginPayload = await loginResponse.json().catch(() => null);
            if (!loginResponse.ok || !loginPayload?.access_token) {
                throw new Error(loginPayload?.detail || '로그인에 실패했습니다.');
            }
            if (typeof window !== 'undefined') {
                localStorage.setItem(CUSTOMER_TOKEN_KEY, loginPayload.access_token);
            }
            setToken(loginPayload.access_token);
            await loadMyInfo(loginPayload.access_token);
            await loadHistory(loginPayload.access_token);
            setAuthMessage(authMode === 'signup' ? '회원가입과 로그인이 완료되었습니다.' : '로그인되었습니다.');
        } catch (error: any) {
            setAuthMessage(error?.message || '인증 처리 중 오류가 발생했습니다.');
        } finally {
            setAuthLoading(false);
        }
    }, [apiBaseUrl, authMode, businessName, businessRegistrationNumber, email, fullName, loadHistory, loadMyInfo, memberType, password, representativeName, username]);

    const createStageRun = React.useCallback(async () => {
        if (!authHeaders) {
            throw new Error('로그인 후 주문을 시작할 수 있습니다.');
        }
        const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/stage-runs`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders,
            },
            body: JSON.stringify({
                task: buildTask(selectedProduct, taskDraft, projectName),
                mode: 'full',
                project_name: projectName.trim() || selectedProduct.id,
            }),
        });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload?.run_id) {
            throw new Error(payload?.detail || '고객 오케스트레이터 stage run 생성에 실패했습니다.');
        }
        setRunId(payload.run_id);
        setStageRun(payload);
        return payload.run_id as string;
    }, [apiBaseUrl, authHeaders, projectName, selectedProduct, taskDraft]);

    const submitOrchestration = React.useCallback(async () => {
        setSubmitLoading(true);
        setErrorText('');
        try {
            const effectiveRunId = runId || await createStageRun();
            const effectiveStageId = stageRun?.current_stage_id || 'ARCH-001';
            const acceptedResponse = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/accepted`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(authHeaders || {}),
                },
                body: JSON.stringify({
                    task: buildTask(selectedProduct, taskDraft, projectName),
                    mode: 'full',
                    project_name: projectName.trim() || selectedProduct.id,
                    stage_run_id: effectiveRunId,
                    stage_id: effectiveStageId,
                }),
            });
            const acceptedPayload = await acceptedResponse.json().catch(() => null) as { accepted?: boolean; stage_run?: SharedOrchestratorStageRun; message?: string } | null;
            if (!acceptedResponse.ok || !acceptedPayload?.accepted) {
                throw new Error((acceptedPayload as any)?.detail || '고객 오케스트레이터 접수에 실패했습니다.');
            }
            if (acceptedPayload.stage_run) {
                setStageRun(acceptedPayload.stage_run);
                setRunId(acceptedPayload.stage_run.run_id);
            }
            if (acceptedPayload.message) {
                setResultText(acceptedPayload.message);
            }

            const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(authHeaders || {}),
                },
                body: JSON.stringify({
                    task: buildTask(selectedProduct, taskDraft, projectName),
                    mode: 'full',
                    project_name: projectName.trim() || selectedProduct.id,
                    stage_run_id: effectiveRunId,
                    stage_id: effectiveStageId,
                }),
            });
            const streamText = await response.text();
            const resultEvent = streamText
                .split('\n\n')
                .map((chunk) => chunk.replace(/^data:\s*/gm, '').trim())
                .filter(Boolean)
                .map((chunk) => {
                    try {
                        return JSON.parse(chunk);
                    } catch {
                        return null;
                    }
                })
                .filter(Boolean)
                .find((item: any) => item?.event === 'result') as { payload?: CustomerOrchestrateResult } | undefined;
            const payload = resultEvent?.payload ?? null;
            if (!response.ok || !payload) {
                throw new Error('고객 오케스트레이터 스트림 실행에 실패했습니다.');
            }
            const result = payload.result || {};
            setResultText(result.final_output || result.completion_summary || '실행 결과가 준비되었습니다.');
            if (result.stage_run) {
                setStageRun(result.stage_run);
                setRunId(result.stage_run.run_id);
            } else {
                await refreshStageRun(effectiveRunId);
            }
            await loadHistory();
        } catch (error: any) {
            setErrorText(error?.message || '고객 오케스트레이터 실행 중 오류가 발생했습니다.');
        } finally {
            setSubmitLoading(false);
        }
    }, [apiBaseUrl, authHeaders, createStageRun, loadHistory, projectName, refreshStageRun, runId, selectedProduct, stageRun?.current_stage_id, taskDraft]);

    const updateStageStatus = React.useCallback(async (status: 'passed' | 'failed' | 'manual_correction') => {
        if (!runId || !stageRun?.current_stage_id || !authHeaders) return;
        setStageLoading(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/stage-runs/update`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    run_id: runId,
                    stage_id: stageRun.current_stage_id,
                    status,
                    note: stageNoteDraft,
                    manual_correction: status === 'manual_correction' ? stageNoteDraft : '',
                    substep_checks: stageSubstepChecks,
                    revision_note: stageRevisionNote,
                }),
            });
            const payload = await response.json().catch(() => null);
            if (!response.ok || !payload) {
                throw new Error(payload?.detail || '단계 상태 업데이트에 실패했습니다.');
            }
            setStageRun(payload);
            setStageNoteDraft('');
            setStageRevisionNote('');
            await loadHistory();
        } catch (error: any) {
            setErrorText(error?.message || '단계 상태 업데이트 중 오류가 발생했습니다.');
        } finally {
            setStageLoading(false);
        }
    }, [apiBaseUrl, authHeaders, loadHistory, runId, stageNoteDraft, stageRevisionNote, stageRun?.current_stage_id, stageSubstepChecks]);

    const sendStageChat = React.useCallback(async () => {
        const content = chatInput.trim();
        if (!content || !authHeaders) return;
        setChatLoading(true);
        setErrorText('');
        try {
            const userMessage: ConversationMessage = {
                role: 'user',
                speaker: '고객',
                content,
                timestamp: new Date().toISOString(),
                step_title: activeStage?.title,
            };
            const nextConversation = [...conversation, userMessage];
            setConversation(nextConversation);
            const response = await fetch(`${apiBaseUrl}/api/marketplace/customer-orchestrate/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders,
                },
                body: JSON.stringify({
                    task: buildTask(selectedProduct, taskDraft, projectName),
                    message: content,
                    conversation: nextConversation,
                    run_id: runId || undefined,
                    stage_id: stageRun?.current_stage_id || undefined,
                    project_name: projectName.trim() || selectedProduct.id,
                    output_dir: generatedProgramSummary?.output_dir || undefined,
                    project_memory: {
                        pending_tasks: [stageNoteDraft, stageRevisionNote].filter(Boolean),
                    },
                    context_tags: ['customer-orchestrator', 'manual-10step'],
                }),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok || !data) {
                throw new Error(data?.detail || '고객 협업 대화 호출에 실패했습니다.');
            }
            setConversation(Array.isArray(data.conversation) ? data.conversation : nextConversation);
            if (data.stage_chat?.pending_revision_note && content.startsWith('/revise')) {
                setStageRevisionNote((prev) => [prev, data.stage_chat.pending_revision_note].filter(Boolean).join('\n'));
            }
            if (content.startsWith('/pass')) {
                await updateStageStatus('passed');
            } else if (content.startsWith('/fix') || content.startsWith('/revise')) {
                await updateStageStatus('manual_correction');
            } else if (content.startsWith('/fail')) {
                await updateStageStatus('failed');
            } else if (content.startsWith('/verify') || content.startsWith('/resume')) {
                await refreshStageRun();
                await loadHistory();
            }
            setChatInput('');
        } catch (error: any) {
            setErrorText(error?.message || '고객 협업 대화 처리 중 오류가 발생했습니다.');
        } finally {
            setChatLoading(false);
        }
    }, [activeStage?.title, apiBaseUrl, authHeaders, chatInput, conversation, generatedProgramSummary?.output_dir, loadHistory, projectName, refreshStageRun, runId, selectedProduct, stageNoteDraft, stageRevisionNote, stageRun?.current_stage_id, taskDraft, updateStageStatus]);

    return (
        <div className="min-h-screen bg-[#0b0f16] px-6 py-10 text-[#e6edf3]">
            <div className="mx-auto max-w-[1680px] space-y-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#58c9ff]">고객용 오케스트레이터</p>
                        <h1 className="mt-3 text-5xl font-bold text-white">상품 주문 오케스트레이터</h1>
                        <p className="mt-4 max-w-[1040px] text-xl leading-relaxed text-[#aab4c0]">
                            관리자와 분리된 독립 고객 오케스트레이터입니다. 로그인 후 상품 주문 문맥으로 단계 카드를 진행하고 결과를 저장합니다.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <a
                            href="/marketplace"
                            onClick={(event) => {
                                event.preventDefault();
                                window.location.assign('/marketplace');
                            }}
                            className="rounded-2xl border border-[#30363d] bg-[#11161d] px-5 py-3 text-base font-semibold text-white no-underline"
                        >
                            마켓플레이스로 돌아가기
                        </a>
                    </div>
                </div>

                <div className="rounded-[24px] border border-[#25304a] bg-[#10182b] p-5">
                    <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#58c9ff]">오케스트레이터 핵심 사용법</p>
                    <div className="mt-3 space-y-2 text-sm text-[#d2d9e3]">
                        <p>1. 프로젝트명과 주문 내용을 입력합니다.</p>
                        <p>2. 시작은 버튼 또는 `/run` 하나만 사용합니다.</p>
                        <p>3. 카드 판정은 `/pass`, `/fix`, `/fail`로 처리합니다.</p>
                        <p>4. 상태 확인은 `/verify`, 질문/수정은 `/ask`, `/search`, `/news`, `/revise`를 사용합니다.</p>
                    </div>
                </div>

                <div className="grid gap-6 xl:grid-cols-[0.7fr_1.3fr]">
                    <section className="space-y-6">
                        <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#58c9ff]">선택 상품</p>
                            <h2 className="mt-4 text-4xl font-bold text-white">{selectedProduct.title}</h2>
                            <p className="mt-4 text-lg leading-8 text-[#aab4c0]">{selectedProduct.summary}</p>
                            <div className="mt-5 flex flex-wrap gap-3">
                                <span className="rounded-full border border-[#30363d] bg-[#0d1117] px-4 py-2 text-sm text-[#d2d9e3]">{selectedProduct.category}</span>
                                <span className="rounded-full border border-[#31c45d] px-4 py-2 text-sm font-bold text-[#31c45d]">{selectedProduct.price}</span>
                            </div>
                        </div>

                        <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                            <div className="flex items-center justify-between gap-3">
                                <p className="text-lg font-semibold text-white">회원 / 내정보</p>
                                {me && <span className="rounded-full border border-[#31c45d] px-3 py-1 text-xs font-semibold text-[#31c45d]">로그인됨</span>}
                            </div>
                            {!me ? (
                                <form
                                    className="mt-4 space-y-3"
                                    onSubmit={(event) => {
                                        event.preventDefault();
                                        void handleAuth();
                                    }}
                                >
                                    <div className="flex gap-2 text-sm">
                                        <button type="button" onClick={() => setAuthMode('login')} className={`rounded-xl px-4 py-2 ${authMode === 'login' ? 'bg-[#2a7cff] text-white' : 'bg-[#0d1117] text-[#c9d1d9]'}`}>로그인</button>
                                        <button type="button" onClick={() => setAuthMode('signup')} className={`rounded-xl px-4 py-2 ${authMode === 'signup' ? 'bg-[#2a7cff] text-white' : 'bg-[#0d1117] text-[#c9d1d9]'}`}>회원가입</button>
                                    </div>
                                    <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="이메일" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                    {authMode === 'signup' && (
                                        <>
                                            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="사용자명" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                            <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="이름 / 담당자명" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                            <select value={memberType} onChange={(e) => setMemberType(e.target.value as CustomerMemberType)} className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white">
                                                <option value="individual">개인</option>
                                                <option value="sole_proprietor">개인사업자</option>
                                                <option value="corporation">법인사업자</option>
                                            </select>
                                            {memberType !== 'individual' && (
                                                <>
                                                    <input value={businessName} onChange={(e) => setBusinessName(e.target.value)} placeholder={memberType === 'corporation' ? '법인명' : '상호명'} className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                                    <input value={businessRegistrationNumber} onChange={(e) => setBusinessRegistrationNumber(e.target.value)} placeholder="사업자등록번호" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                                </>
                                            )}
                                            {memberType === 'corporation' && <input value={representativeName} onChange={(e) => setRepresentativeName(e.target.value)} placeholder="대표자명" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />}
                                        </>
                                    )}
                                    <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="비밀번호" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                    <button type="submit" disabled={authLoading} className="w-full rounded-2xl bg-[#19c3f3] px-5 py-3 text-base font-bold text-[#041018]">
                                        {authLoading ? '처리 중...' : authMode === 'signup' ? '회원가입 후 시작' : '로그인 후 시작'}
                                    </button>
                                    {authMessage && <p className="text-sm text-[#d2d9e3]">{authMessage}</p>}
                                </form>
                            ) : (
                                <div className="mt-4 space-y-2 text-sm text-[#d2d9e3]">
                                    <p>이메일: {me.email}</p>
                                    <p>사용자명: {me.username}</p>
                                    <p>가입 유형: {MEMBER_TYPE_LABELS[(me.member_type as CustomerMemberType) || 'individual']}</p>
                                    {me.business_name && <p>사업자명/법인명: {me.business_name}</p>}
                                    {me.business_registration_number && <p>사업자등록번호: {me.business_registration_number}</p>}
                                    {me.representative_name && <p>대표자명: {me.representative_name}</p>}
                                </div>
                            )}
                        </div>

                        <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                            <p className="text-lg font-semibold text-white">주문 입력</p>
                            <div className="mt-4 space-y-3">
                                {sourceProjectTitle && (
                                    <div className="rounded-2xl border border-[#25304a] bg-[#0f1523] px-4 py-3 text-sm text-[#d2d9e3]">
                                        연결된 마켓 프로젝트: <span className="font-semibold text-white">{sourceProjectTitle}</span>
                                    </div>
                                )}
                                <input value={projectName} onChange={(e) => setProjectName(e.target.value)} placeholder="프로젝트명" className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                <textarea value={taskDraft} onChange={(e) => setTaskDraft(e.target.value)} rows={7} className="w-full rounded-xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white" />
                                <div className="flex flex-wrap gap-3">
                                    <button type="button" onClick={createStageRun} disabled={!token || submitLoading} className="rounded-2xl border border-[#30363d] bg-[#11161d] px-5 py-3 text-base font-semibold text-white">
                                        단계 카드 시작
                                    </button>
                                    <button type="button" onClick={submitOrchestration} disabled={!token || submitLoading} className="rounded-2xl bg-[#2a7cff] px-5 py-3 text-base font-bold text-white">
                                        {submitLoading ? '실행 중...' : '주문하기'}
                                    </button>
                                </div>
                                {errorText && <p className="text-sm text-[#ffb3ad]">{errorText}</p>}
                            </div>
                        </div>
                    </section>

                    <section className="space-y-6">
                        <OrchestratorStageCardPanel
                            tone="customer"
                            title="고객 공용 단계 카드 오케스트레이터"
                            description="실제 고객 오케스트레이터 stage-run을 10단계 반자동 카드 + 협업 대화 기준으로 진행합니다."
                            stageRun={stageRun}
                            stageNoteDraft={stageNoteDraft}
                            onStageNoteDraftChange={setStageNoteDraft}
                            substepChecks={stageSubstepChecks}
                            onSubstepChecksChange={setStageSubstepChecks}
                            revisionNote={stageRevisionNote}
                            onRevisionNoteChange={setStageRevisionNote}
                            stageUpdateLoading={stageLoading}
                            onMarkPassed={() => updateStageStatus('passed')}
                            onMarkManualCorrection={() => updateStageStatus('manual_correction')}
                            onMarkFailed={() => updateStageStatus('failed')}
                            onRefresh={() => refreshStageRun()}
                            operationalVerificationLabel="고객 stage run 새로고침"
                            commandRules={[
                                '로그인 후 주문 입력을 작성하고 Enter 대신 주문하기 버튼으로 실행합니다.',
                                '단계 카드 통과/보정/미통과는 고객이 직접 확인하며 다음 카드로 진행합니다.',
                                '`/ask`, `/search`, `/news`는 동료처럼 질문/검색/주요뉴스 탐색을 수행합니다.',
                                '`/revise`는 중간 설계 변경, `/resume`은 변경 반영 후 흐름 재개입니다.',
                                '완료/로그/재시도 큐는 하단 이력 패널에서 즉시 확인합니다.',
                            ]}
                            conversation={conversation}
                            chatInput={chatInput}
                            onChatInputChange={setChatInput}
                            chatLoading={chatLoading}
                            onSubmitChat={sendStageChat}
                        />

                        <SharedOrchestratorFollowUpCard
                            tone="customer"
                            title="공통 후속 제안 카드"
                            summary="고객 오케스트레이터도 관리자와 같은 기준으로 후속 제안과 우선순위를 표시합니다."
                            scoreLabel="우선순위"
                            scoreValue={customerHistoryStats.cumulativeScore}
                            scoreAxes={[
                                { id: 'severity', label: 'severity', score: customerFollowUpScore.axes.severity, detail: `publish readiness=${generatedProgramSummary?.publish_ready ? 'ready' : 'blocked'}`, tone: generatedProgramSummary?.publish_ready ? 'good' : 'warning' },
                                { id: 'recency', label: 'recency', score: customerFollowUpScore.axes.recency, detail: `active stage=${activeStage?.status || 'idle'}`, tone: activeStage?.status === 'failed' ? 'warning' : 'neutral' },
                                { id: 'approval_risk', label: 'approval_risk', score: customerFollowUpScore.axes.approvalRisk, detail: `approval history=${generatedProgramSummary?.approval_history_count ?? 0}건`, tone: (generatedProgramSummary?.approval_history_count ?? 0) > 0 ? 'warning' : 'good' },
                                { id: 'hard_gate_impact', label: 'hard_gate_impact', score: customerFollowUpScore.axes.hardGateImpact, detail: `delivery gate=${generatedProgramSummary?.delivery_gate_blocked ? 'blocked' : 'open'}`, tone: generatedProgramSummary?.delivery_gate_blocked ? 'warning' : 'good' },
                                { id: 'operational_risk', label: 'operational_risk', score: customerFollowUpScore.axes.operationalRisk, detail: `retry queue=${retryQueue.length}건`, tone: retryQueue.length > 0 ? 'warning' : 'good' },
                                { id: 'self_run_priority', label: 'self_run_priority', score: customerFollowUpScore.axes.selfRunPriority, detail: `stage run=${generatedProgramSummary?.stage_run_status || 'unknown'}`, tone: (generatedProgramSummary?.stage_run_status === 'failed' || generatedProgramSummary?.stage_run_status === 'manual_correction') ? 'warning' : 'neutral' },
                            ]}
                            recommendations={customerFollowUpRecommendations}
                            metrics={[
                                { label: 'publish readiness', value: generatedProgramSummary?.publish_ready ? 'ready' : 'blocked', tone: generatedProgramSummary?.publish_ready ? 'good' : 'warning' },
                                { label: 'delivery gate', value: generatedProgramSummary?.delivery_gate_blocked ? 'blocked' : 'open', tone: generatedProgramSummary?.delivery_gate_blocked ? 'warning' : 'good' },
                                { label: 'retry queue', value: `${retryQueue.length}건`, tone: retryQueue.length > 0 ? 'warning' : 'good' },
                                { label: 'active stage', value: activeStage?.status || 'idle', tone: activeStage?.status === 'failed' ? 'warning' : 'neutral' },
                                { label: '누적 평균', value: `${customerHistoryStats.averageScore}점`, tone: customerHistoryStats.averageScore >= customerHistoryStats.latestScore ? 'warning' : 'good' },
                                { label: '직전 대비', value: `${customerHistoryStats.momentum >= 0 ? '+' : ''}${customerHistoryStats.momentum}점`, tone: customerHistoryStats.momentum > 0 ? 'warning' : 'good' },
                                { label: 'approval history', value: `${generatedProgramSummary?.approval_history_count ?? 0}건`, tone: (generatedProgramSummary?.approval_history_count ?? 0) > 0 ? 'warning' : 'good' },
                                { label: 'stage run', value: generatedProgramSummary?.stage_run_status || 'unknown', tone: (generatedProgramSummary?.stage_run_status === 'failed' || generatedProgramSummary?.stage_run_status === 'manual_correction') ? 'warning' : 'neutral' },
                            ]}
                            trendPoints={[
                                { label: '직전', value: customerHistoryStats.previousScore ?? customerHistoryStats.latestScore },
                                { label: '현재', value: customerHistoryStats.latestScore },
                                { label: '평균', value: customerHistoryStats.averageScore },
                                { label: '피크', value: customerHistoryStats.peakScore },
                            ]}
                            actionLabel="주문하기"
                            actionBusyLabel="실행 중..."
                            actionDisabled={!token || submitLoading}
                            onAction={() => void submitOrchestration()}
                        />

                        <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                            <p className="text-lg font-semibold text-white">실행 결과</p>
                            <pre className="mt-4 whitespace-pre-wrap rounded-2xl border border-[#25304a] bg-[#0f1523] p-4 text-sm leading-7 text-[#d2d9e3]">{resultText || '아직 실행 결과가 없습니다.'}</pre>
                            {generatedProgramSummary && (
                                <div className="mt-4 rounded-2xl border border-[#25304a] bg-[#0f1523] p-4 text-sm text-[#d2d9e3]">
                                    <p className="font-semibold text-white">실프로그램 출고 요약</p>
                                    {generatedProgramSummary.output_dir && <p className="mt-2 break-all">출력 경로: {generatedProgramSummary.output_dir}</p>}
                                    {generatedProgramSummary.output_archive_path && <p className="break-all">출고 ZIP: {generatedProgramSummary.output_archive_path}</p>}
                                    <p>validation profile: {generatedProgramSummary.validation_profile || '-'}</p>
                                    <p>publish readiness: {generatedProgramSummary.publish_ready ? 'ready' : 'blocked'}</p>
                                    <p>shipping zip reproduction: {generatedProgramSummary.shipping_zip_ok ? 'pass' : 'fail'}</p>
                                    {!!generatedProgramSummary.publish_targets?.length && <p>publish targets: {generatedProgramSummary.publish_targets.join(', ')}</p>}
                                    {!!generatedProgramSummary.required_tests?.length && <p>required tests: {generatedProgramSummary.required_tests.join(', ')}</p>}
                                    {generatedProgramSummary.delivery_gate_message && <p className="text-[#ffb3ad]">gate: {generatedProgramSummary.delivery_gate_message}</p>}
                                </div>
                            )}
                        </div>

                        <div className="grid gap-6 xl:grid-cols-3">
                            <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                                <p className="text-lg font-semibold text-white">내 완료 이력</p>
                                <div className="mt-4 space-y-3 text-sm text-[#d2d9e3]">
                                    {completions.length === 0 ? <p>완료 이력이 없습니다.</p> : completions.map((item) => (
                                        <div key={item.id} className="rounded-2xl border border-[#25304a] bg-[#0f1523] p-3">
                                            <p className="font-semibold text-white">{item.project_name}</p>
                                            <p>{item.mode} · 시도 {item.attempts}</p>
                                            <p>{item.gate_passed ? '상품 기준 통과' : '보정 필요'}</p>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                                <p className="text-lg font-semibold text-white">실행 로그</p>
                                <div className="mt-4 space-y-3 text-sm text-[#d2d9e3]">
                                    {logs.length === 0 ? <p>로그가 없습니다.</p> : logs.map((item) => (
                                        <div key={item.id} className="rounded-2xl border border-[#25304a] bg-[#0f1523] p-3">
                                            <p className="font-semibold text-white">{item.message}</p>
                                            <p>{item.flow_id || '-'} / {item.step_id || '-'} / {item.action || '-'}</p>
                                            <p>{item.status}</p>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="rounded-[28px] border border-[#30363d] bg-[#151b23] p-6">
                                <p className="text-lg font-semibold text-white">재시도 큐</p>
                                <div className="mt-4 space-y-3 text-sm text-[#d2d9e3]">
                                    {retryQueue.length === 0 ? <p>재시도 큐가 없습니다.</p> : retryQueue.map((item) => (
                                        <div key={item.id} className="rounded-2xl border border-[#25304a] bg-[#0f1523] p-3">
                                            <p className="font-semibold text-white">{item.queue_name}</p>
                                            <p>{item.status} · 시도 {item.attempt_count || 0}</p>
                                            <p>{item.last_error || '마지막 오류 없음'}</p>
                                            <button
                                                type="button"
                                                onClick={() => void replayRetryQueueItem(item.id)}
                                                className="mt-3 rounded-xl border border-[#2a7cff] px-3 py-2 text-xs font-semibold text-[#9ecbff]"
                                            >
                                                재시도 다시 실행
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
}
