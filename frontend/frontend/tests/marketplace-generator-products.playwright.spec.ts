import { expect, test } from '@playwright/test';

test('marketplace renders API connected stats filters and project grid', async ({ page }) => {
    await page.goto('/marketplace');

    await expect(page.getByTestId('marketplace-main-page')).toBeVisible();
    await expect(page.getByTestId('marketplace-stats-cards')).toBeVisible();
    await expect(page.getByTestId('marketplace-auth-panel')).toBeVisible();
    await expect(page.getByTestId('marketplace-generator-products-legacy')).toBeVisible();
    await expect(page.getByTestId('marketplace-filters')).toBeVisible();
    await expect(page.getByTestId('marketplace-project-grid')).toBeVisible();
    await expect(page.getByRole('heading', { name: '실제 등록 프로젝트', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '4가지 코드생성기 상품', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '회원가입' })).toBeVisible();
    await page.getByRole('button', { name: '회원가입' }).click();
    const memberTypeSelect = page.locator('select').filter({ has: page.locator('option[value="sole_proprietor"]') }).first();
    await expect(memberTypeSelect).toBeVisible();
    await expect(memberTypeSelect).toContainText('개인사업자');
    await expect(memberTypeSelect).toContainText('법인사업자');
    await expect(page.getByPlaceholder('프로젝트 제목/설명 검색')).toBeVisible();
    await expect(page.getByRole('link', { name: '오케스트레이터 주문' }).first()).toBeVisible();
});

test('marketplace links into orchestrator from API connected page', async ({ page }) => {
    await page.goto('/marketplace');
    const firstOrderLink = page.getByRole('link', { name: '오케스트레이터 주문' }).first();
    const href = await firstOrderLink.getAttribute('href');
    expect(href || '').toMatch(/\/marketplace\/orchestrator(\?product=|$)/);
    await firstOrderLink.click();

    await expect(page).toHaveURL(/\/marketplace\/orchestrator/);
    await expect(page.getByRole('heading', { name: '상품 주문 오케스트레이터' })).toBeVisible();
});
