import { expect, test } from '@playwright/test';

const ADMIN_USERNAME = process.env.PLAYWRIGHT_ADMIN_USERNAME ?? '';
const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? '';

async function ensureAdminDashboard(page: import('@playwright/test').Page) {
    await page.goto('/admin');
    if (page.url().includes('/admin/login')) {
        await page.getByTestId('admin-login-email').fill(ADMIN_USERNAME);
        await page.getByTestId('admin-login-password').fill(ADMIN_PASSWORD);
        await page.getByTestId('admin-login-submit').click();
    }
    await page.waitForURL(/\/admin(?:\/)?(?:\?.*)?$/);
    await page.getByText('🩺 관리자 자동 건강상태 / 자가진단 / 자가개선').waitFor();
}

async function openSystemSettingsPanel(page: import('@playwright/test').Page) {
    const sectionToggle = page
        .getByRole('button')
        .filter({ has: page.getByRole('heading', { name: '🧭 전역 .env 설정 패널', exact: true }) })
        .first();
    await sectionToggle.click();
    await expect(sectionToggle).toContainText('접기');
}

test.describe('admin system settings operational verification', () => {
    test('system settings panel shows connected runtime state on first entry', async ({ page }) => {
        await ensureAdminDashboard(page);
        await openSystemSettingsPanel(page);

        await expect(page.getByText('연동 완료')).toBeVisible();
        await expect(page.getByText('미연동 상태')).toHaveCount(0);
        await expect(page.getByText('메인 /admin 통합 제어')).toBeVisible();
        await expect(page.getByText('관리자 도메인')).toBeVisible();
        await expect(page.getByText('API 기준 주소')).toBeVisible();
        await expect(page.getByText('저장 루트')).toBeVisible();
        await expect(page.getByText('현재 LLM 프로필')).toBeVisible();
        await expect(page.getByRole('button', { name: '전역 자동 전환' })).toBeVisible();
        await expect(page.getByRole('button', { name: '설정 새로고침' })).toBeVisible();
    });

    test('system settings panel stays connected after refresh action', async ({ page }) => {
        await ensureAdminDashboard(page);
        await openSystemSettingsPanel(page);

        const refreshButton = page.getByRole('button', { name: '설정 새로고침' });
        await refreshButton.click();
        await expect(refreshButton).toBeVisible();
        await expect(page.getByText('연동 완료')).toBeVisible();
        await expect(page.getByText('관리자 도메인')).toBeVisible();
        await expect(page.getByText('API 기준 주소')).toBeVisible();
    });
});
