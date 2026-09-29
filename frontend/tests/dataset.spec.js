import { test, expect } from '@playwright/test';
import fs from 'node:fs';

test('dataset lesson completes a round and reopens with intact provenance', async ({ page }) => {
  test.skip(!process.env.PEDAGO_DATASET_FIXTURE, 'Opt-in test with a locally extracted dataset lesson.');
  const fixture = JSON.parse(fs.readFileSync(process.env.PEDAGO_DATASET_FIXTURE, 'utf8'));
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByLabel('课题', { exact: true }).fill(fixture.metadata.topic);
  await page.getByLabel('教案正文', { exact: true }).fill(fixture.content);
  await page.getByRole('button', { name: '开始修订' }).click();
  await expect(page.getByRole('button', { name: '生成模拟建议' })).toBeVisible();
  const sid = new URL(page.url()).searchParams.get('session');
  const path = '/api/sessions/' + sid;
  let state = await (await page.request.get(path + '/current-state')).json();
  expect(state.sections.length).toBeGreaterThanOrEqual(1);
  expect(state.sections.length).toBeLessThanOrEqual(10);
  expect(state.sections.map(s => s.current_content).join('')).toBe(fixture.content);
  const titles = state.sections.map(s => s.title);
  let accepted = 0, rejected = 0;
  for (let index = 0; index < titles.length; index++) {
    await page.getByRole('button', { name: '生成模拟建议' }).click();
    await expect(page.getByRole('button', { name: 'Yes · 采纳' }).first()).toBeVisible();
    await page.getByRole('button', { name: 'Yes · 采纳' }).first().click();
    accepted++;
    await page.reload();
    await expect(page.getByText('已采纳', { exact: true }).first()).toBeVisible();
    await page.getByRole('button', { name: 'No · 拒绝' }).click();
    rejected++;
    if (index === 0) await page.screenshot({ path: '../.runtime/dataset-experience-review.png', fullPage: true });
    await page.getByRole('button', { name: index === titles.length - 1 ? '完成本轮' : '下一个单元', exact: false }).click();
  }
  await page.getByRole('button', { name: '结束并查看最终教案' }).click();
  await expect(page.getByRole('heading', { name: '最终教案', exact: true })).toBeVisible();
  const exported = await (await page.request.get(path + '/export')).json();
  expect(exported.state.session.status).toBe('TERMINATED');
  expect(exported.state.lesson_plan_versions.find(v => v.round_number === 0).content).toBe(fixture.content);
  expect(exported.context_snapshots).toHaveLength(titles.length);
  expect(exported.retrieval_records).toHaveLength(titles.length * 2);
  expect(exported.summary.accepts).toBe(accepted);
  expect(exported.summary.rejects).toBe(rejected);
  expect(exported.failures).toHaveLength(0);
  expect(exported.chat_messages[0].content).toBe(fixture.content);
  fs.writeFileSync('../.runtime/dataset-experience-export.json', JSON.stringify(exported, null, 2));
  await page.screenshot({ path: '../.runtime/dataset-experience-final.png', fullPage: true });
  await page.getByRole('button', { name: '开始新的教案' }).click();
  await page.locator('.history-row').filter({ hasText: fixture.metadata.topic }).first().getByRole('button').click();
  await expect(page.getByRole('heading', { name: '最终教案', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '最终教案', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  const capabilities = await (await page.request.get('/api/capabilities')).json();
  fs.writeFileSync('../.runtime/dataset-experience-result.json', JSON.stringify({
    topic: fixture.metadata.topic, source: fixture.source, characters: fixture.content.length,
    sectionTitles: titles, accepted, rejected, sessionId: sid, capabilities,
    contextSnapshots: exported.context_snapshots.length, retrievalRecords: exported.retrieval_records.length,
    chatMessages: exported.chat_messages.length, browserErrors: errors,
    provider: exported.state.session.config_snapshot.provider,
  }, null, 2));
});
