import { expect, test } from '@playwright/test';

const MARKETPLACE_BASE_URL = process.env.PLAYWRIGHT_MARKETPLACE_BASE_URL ?? 'http://localhost:3000';

const FIXTURES = {
    categories: [
        { id: 1, name: 'AI 엑셀 시트', description: '시트 생성' },
    ],
    projects: {
        projects: [
            {
                id: 201,
                title: '테스트 스프레드시트 프로젝트',
                description: 'Playwright marketplace spreadsheet fixture',
                price: 89000,
                category_id: 1,
                downloads: 7,
                rating: 4.9,
                is_active: true,
                tags: [{ id: 11, name: 'ai-sheet' }],
                category: { id: 1, name: 'AI 엑셀 시트', description: '시트 생성' },
            },
        ],
        total: 1,
        skip: 0,
        limit: 24,
    },
    overview: {
        projects: 1,
        users: 5,
        purchases: 3,
        reviews: 2,
    },
    revenue: {
        total_revenue: 267000,
        total_purchases: 3,
        average_purchase_amount: 89000,
    },
    topProjects: [
        { id: 201, title: '테스트 스프레드시트 프로젝트', downloads: 7, rating: 4.9, price: 89000 },
    ],
    featureCatalog: [
        {
            feature_id: 'ai-sheet',
            title: 'AI 엑셀 시트',
            summary: '시트 schema preview 와 final workbook 패키지를 생성합니다.',
            popup_mode: 'spreadsheet-builder',
            status: 'enabled',
            supports_photo_upload: false,
            supports_final_phase: true,
        },
        {
            feature_id: 'ai-image',
            title: 'AI 이미지',
            summary: '이미지 생성기',
            popup_mode: 'hybrid-image',
            status: 'enabled',
            supports_photo_upload: true,
            supports_final_phase: true,
        },
    ],
};

async function installMarketplaceSpreadsheetMock(page: Parameters<typeof test>[0]['page']) {
    await page.addInitScript(({ fixtures }) => {
        const streamBody = [
            {
                event: 'state',
                payload: {
                    run_id: 'run-ai-sheet-001',
                    state: 'preview_running',
                    progress: { percent: 12, step: 'preview_started', state: 'preview_running', message: 'sheet schema 생성을 시작합니다.', updated_at: '2026-04-22T06:30:01.000Z' },
                },
            },
            {
                event: 'artifact',
                payload: {
                    run_id: 'run-ai-sheet-001',
                    state: 'preview_ready',
                    artifact: {
                        artifact_id: 'sheet-preview-001',
                        artifact_type: 'spreadsheet',
                        phase: 'preview',
                        state: 'preview_ready',
                        title: 'Spreadsheet Preview Artifact',
                        prompt_summary: '영업 리드 관리용 시트 schema preview',
                        keywords: ['sales', 'pipeline', 'spreadsheet'],
                        sheet_schema: {
                            sheet_name: 'SalesPipeline',
                            row_goal: 12,
                            columns: [
                                { name: '고객사명', type: 'text' },
                                { name: '담당자', type: 'text' },
                                { name: '예상매출', type: 'number' },
                                { name: '미팅일', type: 'date' },
                                { name: '진행상태', type: 'text' },
                            ],
                        },
                        generated_at: '2026-04-22T06:30:02.000Z',
                    },
                    progress: { percent: 48, step: 'preview_ready', state: 'preview_ready', message: 'sheet schema preview 가 준비되었습니다.', updated_at: '2026-04-22T06:30:02.000Z' },
                },
            },
            {
                event: 'state',
                payload: {
                    run_id: 'run-ai-sheet-001',
                    state: 'final_running',
                    progress: { percent: 72, step: 'final_started', state: 'final_running', message: 'workbook 패키징을 진행합니다.', updated_at: '2026-04-22T06:30:03.000Z' },
                },
            },
            {
                event: 'quality_review',
                payload: {
                    run_id: 'run-ai-sheet-001',
                    state: 'quality_review',
                    quality_review: {
                        passed: true,
                        score: 0.98,
                        issues: [],
                    },
                    progress: { percent: 88, step: 'quality_review', state: 'quality_review', message: 'spreadsheet quality 검토를 진행합니다.', updated_at: '2026-04-22T06:30:04.000Z' },
                },
            },
            {
                event: 'completed',
                payload: {
                    run_id: 'run-ai-sheet-001',
                    state: 'completed',
                    artifact_manifest: {
                        preview_artifact: {
                            artifact_id: 'sheet-preview-001',
                            artifact_type: 'spreadsheet',
                            phase: 'preview',
                            state: 'preview_ready',
                            title: 'Spreadsheet Preview Artifact',
                            prompt_summary: '영업 리드 관리용 시트 schema preview',
                            keywords: ['sales', 'pipeline', 'spreadsheet'],
                            sheet_schema: {
                                sheet_name: 'SalesPipeline',
                                row_goal: 12,
                                columns: [
                                    { name: '고객사명', type: 'text' },
                                    { name: '담당자', type: 'text' },
                                    { name: '예상매출', type: 'number' },
                                    { name: '미팅일', type: 'date' },
                                    { name: '진행상태', type: 'text' },
                                ],
                            },
                            generated_at: '2026-04-22T06:30:02.000Z',
                        },
                        final_artifact: {
                            artifact_id: 'sheet-final-001',
                            artifact_type: 'spreadsheet',
                            phase: 'final',
                            state: 'completed',
                            title: 'Spreadsheet Final Artifact',
                            prompt_summary: '영업 리드 관리용 workbook final package',
                            keywords: ['sales', 'pipeline', 'xlsx'],
                            sheet_schema: {
                                sheet_name: 'SalesPipeline',
                                row_goal: 12,
                                columns: [
                                    { name: '고객사명', type: 'text' },
                                    { name: '담당자', type: 'text' },
                                    { name: '예상매출', type: 'number' },
                                    { name: '미팅일', type: 'date' },
                                    { name: '진행상태', type: 'text' },
                                ],
                            },
                            workbook: {
                                sheet_name: 'SalesPipeline',
                                column_count: 5,
                                row_count: 12,
                                sample_rows: [
                                    { 고객사명: 'Contoso', 담당자: '김민수', 예상매출: 1200000, 미팅일: '2026-04-22', 진행상태: '협상중' },
                                    { 고객사명: 'Fabrikam', 담당자: '이서연', 예상매출: 850000, 미팅일: '2026-04-24', 진행상태: '초기접촉' },
                                    { 고객사명: 'Northwind', 담당자: '박지훈', 예상매출: 2300000, 미팅일: '2026-05-02', 진행상태: '제안발송' },
                                ],
                            },
                            delivery_assets: [
                                {
                                    format: 'xlsx',
                                    path: '/tmp/SalesPipeline.xlsx',
                                    path_hint: 'SalesPipeline.xlsx',
                                    size_bytes: 24576,
                                    exists: true,
                                    generated_at: '2026-04-22T06:30:06.000Z',
                                },
                                {
                                    format: 'csv',
                                    path: '/tmp/SalesPipeline.csv',
                                    path_hint: 'SalesPipeline.csv',
                                    size_bytes: 6144,
                                    exists: true,
                                    generated_at: '2026-04-22T06:30:05.000Z',
                                },
                            ],
                            generated_at: '2026-04-22T06:30:06.000Z',
                            notes: ['xlsx 결과물이 최신 생성 파일입니다.'],
                        },
                    },
                    quality_review: {
                        passed: true,
                        score: 0.98,
                        issues: [],
                    },
                    progress: { percent: 100, step: 'completed', state: 'completed', message: 'spreadsheet 결과물이 준비되었습니다.', updated_at: '2026-04-22T06:30:06.000Z' },
                },
            },
        ].map((item) => `data: ${JSON.stringify(item)}\n\n`).join('');

        const originalFetch = window.fetch.bind(window);
        window.fetch = async (input, init) => {
            const requestUrl = typeof input === 'string' ? input : input instanceof Request ? input.url : String(input);
            const url = new URL(requestUrl, window.location.origin);
            const path = url.pathname;

            const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
                status,
                headers: { 'Content-Type': 'application/json' },
            });

            if (path.endsWith('/api/marketplace/categories')) {
                return jsonResponse(fixtures.categories);
            }
            if (path.endsWith('/api/marketplace/projects')) {
                return jsonResponse(fixtures.projects);
            }
            if (path.endsWith('/api/marketplace/stats/overview')) {
                return jsonResponse(fixtures.overview);
            }
            if (path.endsWith('/api/marketplace/stats/revenue')) {
                return jsonResponse(fixtures.revenue);
            }
            if (path.endsWith('/api/marketplace/stats/top-projects')) {
                return jsonResponse(fixtures.topProjects);
            }
            if (path.endsWith('/api/marketplace/feature-catalog')) {
                return jsonResponse(fixtures.featureCatalog);
            }
            if (path.endsWith('/api/marketplace/feature-orchestrate/accepted')) {
                return jsonResponse({
                    run_id: 'run-ai-sheet-001',
                    stage_run: {
                        run_id: 'run-ai-sheet-001',
                        current_stage_id: 'preview',
                        status: 'running',
                        final_completed: false,
                    },
                });
            }
            if (path.includes('/api/marketplace/feature-orchestrate/stage-runs/')) {
                return jsonResponse({
                    run_id: 'run-ai-sheet-001',
                    current_stage_id: 'quality_review',
                    status: 'completed',
                    final_completed: true,
                });
            }
            if (path.endsWith('/api/marketplace/feature-orchestrate/stream')) {
                return new Response(streamBody, {
                    status: 200,
                    headers: {
                        'Content-Type': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        Connection: 'keep-alive',
                    },
                });
            }

            return originalFetch(input, init);
        };
    }, {
        fixtures: FIXTURES,
    });
}

test('marketplace ai-sheet launcher opens popup and renders spreadsheet workbook/download polish', async ({ page }) => {
    await installMarketplaceSpreadsheetMock(page);

    await page.goto(`${MARKETPLACE_BASE_URL}/marketplace`);

    await expect(page.getByTestId('marketplace-feature-launcher-grid')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('marketplace-feature-card-ai-sheet')).toContainText('AI 엑셀 시트');
    await expect(page.getByTestId('marketplace-feature-card-ai-sheet')).toContainText('schema preview');
    await expect(page.getByTestId('marketplace-feature-card-ai-sheet')).toContainText('xlsx/csv 다운로드');

    await page.getByTestId('marketplace-feature-launch-ai-sheet').click();

    await expect(page.getByTestId('marketplace-feature-orchestrator-popup')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('marketplace-popup-project-name')).toHaveValue('marketplace-sheet-run');
    await page.getByTestId('marketplace-popup-project-name').fill('playwright-spreadsheet-check');
    await page.getByTestId('marketplace-popup-prompt').fill('영업 리드 관리용 스프레드시트 workbook 을 생성한다. 숫자와 날짜 정렬을 검증한다.');
    await page.getByTestId('marketplace-popup-submit').click();

    await expect(page.getByTestId('marketplace-popup-run-id')).toContainText('run-ai-sheet-001');
    await expect(page.getByTestId('marketplace-live-view-sheet-summary')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('marketplace-live-view-current-state')).toContainText('완료');
    await expect(page.getByTestId('marketplace-progress-percent')).toContainText('100%');
    await expect(page.getByTestId('marketplace-progress-message')).toContainText('spreadsheet 결과물이 준비되었습니다.');
    await expect(page.getByTestId('marketplace-progress-milestones')).toContainText('sheet schema 생성을 시작합니다.');
    await expect(page.getByTestId('marketplace-progress-milestones')).toContainText('sheet schema preview 가 준비되었습니다.');
    await expect(page.getByTestId('marketplace-progress-milestones')).toContainText('workbook 패키징을 진행합니다.');
    await expect(page.getByTestId('marketplace-progress-milestones')).toContainText('spreadsheet quality 검토를 진행합니다.');

    await expect(page.getByTestId('marketplace-final-artifact-card')).toContainText('Final Artifact');
    await expect(page.getByTestId('marketplace-final-artifact-card')).toContainText('영업 리드 관리용 workbook final package');
    await expect(page.getByTestId('marketplace-preview-artifact-card').getByTestId('marketplace-schema-preview-table')).toBeVisible();
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-schema-preview-table')).toBeVisible();
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-sample-rows')).toBeVisible();
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-row-number-header')).toContainText('행');
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-row-number-1')).toContainText('1');
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-row-number-2')).toContainText('2');
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-sample-rows')).toContainText('Contoso');
    await expect(page.getByTestId('marketplace-final-artifact-card').getByTestId('marketplace-workbook-sample-rows')).toContainText('2026-04-22');
    await expect(page.getByTestId('marketplace-spreadsheet-downloads')).toBeVisible();
    await expect(page.getByTestId('marketplace-spreadsheet-download-xlsx')).toContainText('playwright-spreadsheet-check.xlsx');
    await expect(page.getByTestId('marketplace-spreadsheet-download-csv')).toContainText('playwright-spreadsheet-check.csv');
    await expect(page.getByTestId('marketplace-spreadsheet-download-latest-badge-xlsx')).toContainText('최근 생성 파일');
    await expect(page.getByTestId('marketplace-quality-gate')).toContainText('98점');
});
