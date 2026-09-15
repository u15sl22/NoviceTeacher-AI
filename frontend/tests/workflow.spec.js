import { test, expect } from "@playwright/test";

test("professor demo: paste, accept/reject, refresh, next section, two rounds, terminate, versions and export", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "导入你的教案" }),
  ).toBeVisible();
  await page.screenshot({ path: "../.runtime/input.png", fullPage: true });
  await page.getByLabel("课题", { exact: true }).fill("分数的初步认识");
  await page
    .getByLabel("教案正文", { exact: true })
    .fill("教学目标\n理解分数。\n\n课堂小结\n教师总结本课内容。");
  await page.getByRole("button", { name: "开始修订" }).click();
  await expect(
    page.getByRole("button", { name: "生成模拟建议" }),
  ).toBeVisible();
  const resumeUrl = page.url();
  await page.getByRole("button", { name: "生成模拟建议" }).click();
  await expect(page.getByRole("button", { name: "Yes · 采纳" })).toHaveCount(2);
  await page.getByRole("button", { name: "Yes · 采纳" }).first().click();
  await expect(page.locator(".content-panel .lesson-text")).toContainText(
    "请学生用自己的话解释",
  );
  await page.reload();
  await expect(page.getByText("已采纳", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "No · 拒绝" })).toHaveCount(1);
  await page.getByRole("button", { name: "No · 拒绝" }).click();
  await page.screenshot({ path: "../.runtime/review.png", fullPage: true });
  await page.getByRole("button", { name: "下一个单元" }).click();
  await page.getByRole("button", { name: "生成模拟建议" }).click();
  await page.getByRole("button", { name: "Yes · 采纳" }).first().click();
  await expect(page.getByRole("button", { name: "Yes · 采纳" })).toHaveCount(1);
  await page.getByRole("button", { name: "Yes · 采纳" }).click();
  await page.getByRole("button", { name: "完成本轮", exact: false }).click();
  await expect(page.getByRole("button", { name: "继续第 2 轮" })).toBeVisible();
  await page.getByRole("button", { name: "继续第 2 轮" }).click();
  for (const last of [false, true]) {
    await page.getByRole("button", { name: "生成模拟建议" }).click();
    await expect(
      page.getByRole("heading", { name: "本单元暂无新增建议" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: last ? "完成本轮" : "下一个单元" })
      .click();
  }
  await page.getByRole("button", { name: "结束并查看最终教案" }).click();
  await expect(
    page.getByRole("heading", { name: "最终教案", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "最终教案", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".final-text")).toContainText(
    "请学生用自己的话解释",
  );
  await page.getByText("当前最新版", { exact: true }).click();
  await page.getByRole("option", { name: "V0 · 原始教案" }).click();
  await expect(page.locator(".final-text")).not.toContainText(
    "请学生用自己的话解释",
  );
  const response = await page.request.get(
    "/api/sessions/" +
      new URL(resumeUrl).searchParams.get("session") +
      "/export",
  );
  const data = await response.json();
  expect(data.state.session.status).toBe("TERMINATED");
  expect(data.decisions).toHaveLength(4);
  expect(data.state.lesson_plan_versions).toHaveLength(3);
  expect(
    data.interaction_events.some((e) => e.event_type === "SECTION_VIEWED"),
  ).toBeTruthy();
  await page.screenshot({ path: "../.runtime/final.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("mobile input fits viewport and recovers from a network error", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "填入数学示例" }).click();
  await page.route("**/api/sessions", (route) => route.abort());
  await page.getByRole("button", { name: "开始修订" }).click();
  await expect(
    page.getByText("网络连接失败。", { exact: false }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "重试原提交" })).toBeVisible();
  await page.unroute("**/api/sessions");
  await page.getByRole("button", { name: "重试原提交" }).click();
  await expect(
    page.getByRole("button", { name: "生成模拟建议" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "../.runtime/mobile.png", fullPage: true });
});
