export async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "提交内容不完整，请检查输入。",
      );
    return data;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "请求等待超时。服务器可能仍在处理，请点击“恢复最新状态”后重试。",
      );
    if (error instanceof TypeError)
      throw new Error(
        "网络连接失败。已保存的进度不会丢失，请恢复最新状态后重试。",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
