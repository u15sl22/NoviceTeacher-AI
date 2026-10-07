<script setup>
import { computed, nextTick, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { request, uploadDocument } from "./api";
import { sample } from "./sample";

const state = ref(null),
  health = ref(null),
  busy = ref(false),
  error = ref(""),
  booting = ref(true);
const form = ref({ subject: "数学", grade: "三年级", topic: "", content: "", model_profile: "" });
const fileInput = ref(null), uploadedDocument = ref(null), documentBusy = ref(false);
const savedId = ref(
  new URLSearchParams(location.search).get("session") ||
    localStorage.getItem("pedago.session") ||
    "",
);
const recoveryId = ref(savedId.value);
const pendingKey = ref(localStorage.getItem("pedago.pending-key") || "");
const selectedVersion = ref(null);
const mySessions = ref([]), currentUser = ref(null), capabilities = ref(null);
async function loadHistory() {
  currentUser.value = await request('/me');
  mySessions.value = await request('/sessions');
  capabilities.value = await request('/capabilities');
  if (!form.value.model_profile) {
    form.value.model_profile = capabilities.value.model_profiles?.find((item) => item.default)?.id || "";
  }
}
const current = computed(() =>
  state.value?.sections.find(
    (s) => s.id === state.value.session.current_section_id,
  ),
);
const status = computed(() => state.value?.session.status);
const provider = computed(
  () => state.value?.session.config_snapshot.provider || health.value?.provider,
);
const modelLabel = computed(() => {
  if (state.value?.session.config_snapshot.model_label)
    return state.value.session.config_snapshot.model_label;
  const profile = capabilities.value?.model_profiles?.find(
    (item) => item.id === form.value.model_profile,
  );
  return profile?.label || (provider.value === "mock" ? "模拟演示" : "Generic AI");
});
const maxRounds = computed(
  () => state.value?.session.config_snapshot.max_rounds || 5,
);
const completed = computed(
  () => state.value?.sections.filter((s) => s.review.completed_at).length || 0,
);
const canNext = computed(
  () =>
    current.value?.review.generated_at &&
    current.value.suggestions.every((s) => s.decision),
);
const path = computed(() => "/sessions/" + state.value?.session.id);
const finalContent = computed(() =>
  selectedVersion.value === null
    ? state.value?.lesson_plan.current_content
    : state.value?.lesson_plan_versions.find(
        (v) => v.round_number === selectedVersion.value,
      )?.content,
);
const stage = computed(() =>
  !state.value
    ? 0
    : status.value === "ACTIVE"
      ? 1
      : status.value === "ROUND_COMPLETED"
        ? 2
        : 3,
);
const target = () => ({
  round_id: state.value.round.id,
  section_id: current.value.id,
});

function persistState(data) {
  state.value = data;
  savedId.value = data.session.id;
  recoveryId.value = data.session.id;
  localStorage.setItem("pedago.session", data.session.id);
  history.replaceState(null, "", "?session=" + data.session.id);
}
async function logView() {
  if (!current.value) return;
  await nextTick();
  await request(path.value + "/view", {
    ...target(),
    section_version_id: current.value.version.id,
    suggestion_ids: current.value.suggestions.map((s) => s.id),
    decision_ids: current.value.suggestions
      .filter((s) => s.decision)
      .map((s) => s.decision.id),
  });
}
async function run(action) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function recover(id = savedId.value) {
  if (!id.trim()) return;
  await run(async () => {
    persistState(
      await request(
        "/sessions/" + encodeURIComponent(id.trim()) + "/current-state",
      ),
    );
    await logView();
  });
}
function useSample() {
  uploadedDocument.value = null;
  const model_profile = form.value.model_profile;
  form.value = {
    subject: "数学",
    grade: "三年级",
    topic: "分数的初步认识",
    content: sample,
    model_profile,
  };
}
async function selectDocument(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  const suffix = file.name.toLowerCase().split(".").pop();
  if (!["docx", "pdf"].includes(suffix)) {
    error.value = "请选择 .docx 或 .pdf 文件。";
    return;
  }
  if (file.size > 15 * 1024 * 1024) {
    error.value = "文件超过 15 MB 限制。";
    return;
  }
  documentBusy.value = true;
  error.value = "";
  try {
    const parsed = await uploadDocument(file);
    uploadedDocument.value = parsed;
    form.value.content = parsed.text;
    if (!form.value.topic.trim())
      form.value.topic = parsed.filename.replace(/\.(docx|pdf)$/i, "");
    ElMessage.success("文件已保存，正文已提取，请确认后开始修订");
  } catch (e) {
    error.value = e.message;
  } finally {
    documentBusy.value = false;
  }
}
async function start() {
  if (Object.values(form.value).some((v) => !v.trim())) {
    error.value = "请填写学科、年级、课题和教案正文。";
    return;
  }
  await run(async () => {
    // Keep both payload and key on ambiguous network failures, including after refresh.
    const cached = localStorage.getItem("pedago.pending-payload");
    let payload;
    if (pendingKey.value && cached) payload = JSON.parse(cached);
    else {
      pendingKey.value = crypto.randomUUID();
      payload = {
        request_key: pendingKey.value,
        metadata: {
          subject: form.value.subject,
          grade: form.value.grade,
          topic: form.value.topic,
        },
        content: form.value.content,
        document_id: uploadedDocument.value?.id || null,
        model_profile: form.value.model_profile || null,
      };
      localStorage.setItem("pedago.pending-key", pendingKey.value);
      localStorage.setItem("pedago.pending-payload", JSON.stringify(payload));
    }
    persistState(await request("/sessions", payload));
    pendingKey.value = "";
    localStorage.removeItem("pedago.pending-key");
    localStorage.removeItem("pedago.pending-payload");
    await logView();
  });
}
async function mutate(suffix, body = {}) {
  await run(async () => {
    persistState(await request(path.value + suffix, body));
    await logView();
  });
}
function newLesson() {
  state.value = null;
  selectedVersion.value = null;
  error.value = "";
  uploadedDocument.value = null;
  history.replaceState(null, "", location.pathname);
  run(loadHistory);
}
function downloadText() {
  const blob = new Blob([finalContent.value], {
    type: "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = "PedagoLoop-教案.txt";
  a.click();
  URL.revokeObjectURL(url);
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href);
    ElMessage.success("恢复链接已复制");
  } catch {
    error.value = "无法访问剪贴板，请复制浏览器地址栏中的恢复链接。";
  }
}
onMounted(async () => {
  const pending = localStorage.getItem("pedago.pending-payload");
  if (pending) {
    try {
      const p = JSON.parse(pending);
      form.value = { ...p.metadata, content: p.content, model_profile: p.model_profile || "" };
      if (p.document_id) uploadedDocument.value = { id: p.document_id, filename: "已上传的源文件" };
    } catch {
      /* keep normal form */
    }
  }
  try {
    health.value = await request("/health");
    await loadHistory();
  } catch (e) {
    error.value = e.message;
  }
  if (savedId.value) await recover();
  booting.value = false;
});
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" @click.prevent="!busy && newLesson()"
        ><span class="brand-mark">p<span>↻</span></span
        ><span>PedagoLoop<small>教案修订工作台</small></span></a
      >
      <div class="top-meta">
        <span class="edition">INTERNAL PILOT · ALPHA</span
        ><el-tag
          :type="provider === 'mock' ? 'warning' : 'success'"
          effect="plain"
          >{{
            provider === "mock"
              ? "模拟演示 · 非真实 AI"
              : provider === "generic_llm"
                ? modelLabel
                : "连接中"
          }}</el-tag
        >
      </div>
    </header>
    <main v-loading="booting">
      <nav class="steps" aria-label="修订流程">
        <div
          v-for="(label, i) in ['导入教案', '逐节修订', '轮次回顾', '最终教案']"
          :key="label"
          :class="{ active: stage === i, done: stage > i }"
        >
          <span>{{ stage > i ? "✓" : "0" + (i + 1) }}</span
          >{{ label }}
        </div>
      </nav>
      <el-alert
        v-if="error"
        class="error"
        type="error"
        :title="error"
        show-icon
        :closable="false"
        ><el-button v-if="savedId" text @click="recover()" :disabled="busy"
          >恢复最新状态</el-button
        ></el-alert
      >

      <template v-if="!state && !booting">
        <el-alert v-if="capabilities" type="info" :closable="false" style="margin-bottom:20px"
          :title="`案例库：${capabilities.verified_cases} 条已核验；知识库：${capabilities.verified_knowledge} 条已核验；${capabilities.raw_annotations} 条原始批注未进入检索。`" />
        <div class="intro">
          <p class="eyebrow">REFLECT. REVISE. GROW.</p>
          <h1>让每一次修订，<br />都有迹可循。</h1>
          <p class="lead">
            从一份已有的教案开始，逐节审视教学设计。<br />参考建议，自主决定，保留每一步思考与改进。
          </p>
        </div>
        <div class="input-layout">
          <section class="panel input-panel">
            <div class="panel-heading">
              <div>
                <span class="eyebrow">01 / LESSON PLAN</span>
                <h2>导入你的教案</h2>
              </div>
              <el-button
                text
                @click="useSample"
                :disabled="busy || !!pendingKey"
                >填入数学示例 ↗</el-button
              >
            </div>
            <el-form
              label-position="top"
              @submit.prevent="start"
              :disabled="busy || !!pendingKey"
            >
              <div class="fields">
                <el-form-item label="学科"
                  ><el-input
                    v-model="form.subject"
                    maxlength="100"
                    placeholder="例如：数学"
                    aria-label="学科" /></el-form-item
                ><el-form-item label="年级"
                  ><el-input
                    v-model="form.grade"
                    maxlength="100"
                    placeholder="例如：三年级"
                    aria-label="年级"
                /></el-form-item>
              </div>
              <el-form-item label="课题 / Lesson topic"
                ><el-input
                  v-model="form.topic"
                  maxlength="200"
                  placeholder="例如：分数的初步认识"
                  aria-label="课题"
              /></el-form-item>
              <el-form-item v-if="capabilities?.model_profiles?.length" label="本次修订使用的模型">
                <el-select v-model="form.model_profile" style="width:100%" aria-label="模型选择">
                  <el-option
                    v-for="item in capabilities.model_profiles"
                    :key="item.id"
                    :value="item.id"
                    :label="`${item.label} · ${item.model}${item.configured ? '' : '（未配置密钥）'}`"
                    :disabled="!item.configured"
                  />
                </el-select>
                <p class="muted model-help">模型在会话创建后固定；切换配置不会改变已有会话。</p>
              </el-form-item>
              <div class="document-upload">
                <input
                  ref="fileInput"
                  class="visually-hidden"
                  type="file"
                  accept=".docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  @change="selectDocument"
                />
                <div>
                  <strong>从 Word / PDF 导入</strong>
                  <p class="muted">支持 DOCX 和文本型 PDF，最大 15 MB。扫描版 PDF 需要先做 OCR。</p>
                </div>
                <el-button
                  :loading="documentBusy"
                  :disabled="busy || !!pendingKey"
                  @click="fileInput?.click()"
                >选择文件</el-button>
              </div>
              <div v-if="uploadedDocument" class="document-result">
                <div>
                  <strong>{{ uploadedDocument.filename }}</strong>
                  <p class="muted">
                    已安全保存<span v-if="uploadedDocument.char_count"> · 提取 {{ uploadedDocument.char_count }} 字</span><span v-if="uploadedDocument.page_count"> · {{ uploadedDocument.page_count }} 页</span> · 可在下方校对正文
                  </p>
                </div>
                <a
                  v-if="uploadedDocument.id && uploadedDocument.filename !== '已上传的源文件'"
                  :href="'/api/documents/' + uploadedDocument.id + '/download'"
                  download
                >下载原文件</a>
              </div>
              <el-form-item label="教案正文"
                ><el-input
                  v-model="form.content"
                  type="textarea"
                  :rows="12"
                  maxlength="100000"
                  show-word-limit
                  placeholder="粘贴已有教案，可保留教学目标、导入、练习等标题……"
                  aria-label="教案正文"
              /></el-form-item>
            </el-form>
            <p v-if="pendingKey" class="muted">
              有一份待确认的提交，重试会使用原提交编号，避免创建重复会话。
            </p>
            <div class="panel-footer">
              <span class="muted">自动识别教学单元 · 支持粘贴文本、DOCX、文本型 PDF</span
              ><el-button
                type="primary"
                size="large"
                :loading="busy || documentBusy"
                :disabled="documentBusy"
                @click="start"
                >{{ pendingKey ? "重试原提交" : "开始修订" }} →</el-button
              >
            </div>
          </section>
          <aside class="intro-aside">
            <p class="eyebrow">A THOUGHTFUL REVISION LOOP</p>
            <h2>你来判断，<br />系统记录。</h2>
            <div class="principle">
              <b>01</b>
              <div>
                <h3>聚焦一个教学单元</h3>
                <p>每次最多两条建议，留出认真思考的空间。</p>
              </div>
            </div>
            <div class="principle">
              <b>02</b>
              <div>
                <h3>每条建议，自主选择</h3>
                <p>采纳后安全替换或补充正文，拒绝也会完整保留。</p>
              </div>
            </div>
            <div class="principle">
              <b>03</b>
              <div>
                <h3>逐轮回看改进</h3>
                <p>最多五轮，随时通过恢复链接返回进度。</p>
              </div>
            </div>
            <div class="aside-note">
              当前为内部试用版。只有已核验资料可参与检索；模型解释与原始来源分别标注。
            </div>
          </aside>
        </div>
        <section class="recovery panel">
          <div>
            <h3>继续已有修订</h3>
            <p class="muted">粘贴会话编号，恢复数据库中保存的进度。</p>
          </div>
          <el-input
            v-model="recoveryId"
            placeholder="Session ID"
            aria-label="会话编号"
          /><el-button
            :disabled="busy || !recoveryId"
            @click="recover(recoveryId)"
            >恢复会话</el-button
          >
        </section>
        <section class="panel" style="padding:24px;margin-top:24px">
          <h3>我的历史 <span class="muted">{{ currentUser?.username }}</span></h3>
          <p v-if="!mySessions.length" class="muted">当前用户还没有修订记录。</p>
          <div v-for="item in mySessions" :key="item.id" class="history-row">
            <div><strong>{{ item.lesson_metadata.topic }}</strong><p class="muted">{{ item.lesson_metadata.subject }} · {{ item.lesson_metadata.grade }} · {{ item.config_snapshot.model_label || item.config_snapshot.model }} · {{ { CREATED: '已创建', ACTIVE: '修订中', ROUND_COMPLETED: '本轮已完成', TERMINATED: '已结束' }[item.status] || item.status }} · {{ new Date(item.updated_at).toLocaleString() }}</p></div>
            <el-button :disabled="busy" @click="recover(item.id)">查看 / 继续</el-button>
          </div>
        </section>
      </template>

      <template v-else-if="state">
        <div class="workspace-heading">
          <div>
            <p class="eyebrow">
              {{ state.session.lesson_metadata.subject }} /
              {{ state.session.lesson_metadata.grade }}
            </p>
            <h1>{{ state.session.lesson_metadata.topic }}</h1>
            <p class="muted">
              第 {{ state.round.round_number }} / {{ maxRounds }} 轮 ·
              {{
                status === "TERMINATED"
                  ? "会话已完成"
                  : status === "ROUND_COMPLETED"
                    ? "本轮已完成"
                    : "逐节修订中"
              }} · {{ state.session.config_snapshot.model_label || state.session.config_snapshot.model }}
            </p>
          </div>
          <div class="toolbar">
            <a
              v-if="state.source_document"
              :href="'/api/documents/' + state.source_document.id + '/download'"
              download
            >下载源文件</a>
            <el-button @click="copyLink">复制恢复链接</el-button
            ><el-button :disabled="busy" @click="recover()"
              >恢复最新状态</el-button
            >
          </div>
        </div>
        <div v-if="status === 'ACTIVE' && current" class="review-layout">
          <aside class="section-nav panel">
            <span class="eyebrow">LESSON SECTIONS</span>
            <h3>
              教学单元
              <span class="muted"
                >{{ completed }} / {{ state.sections.length }}</span
              >
            </h3>
            <el-progress
              :percentage="
                Math.round((completed / state.sections.length) * 100)
              "
              :show-text="false"
            />
            <ol>
              <li
                v-for="(section, i) in state.sections"
                :key="section.id"
                :class="{
                  selected: section.id === current.id,
                  completed: section.review.completed_at,
                }"
              >
                <span>{{
                  section.review.completed_at
                    ? "✓"
                    : String(i + 1).padStart(2, "0")
                }}</span
                ><span>{{ section.title }}</span>
              </li>
            </ol>
            <p class="muted nav-note">
              按顺序完成每个单元。<br />进度已保存在服务器。
            </p>
          </aside>
          <div class="review-main">
            <section class="panel content-panel">
              <div class="panel-heading">
                <div>
                  <span class="eyebrow"
                    >SECTION {{ current.order_index + 1 }} /
                    {{ state.sections.length }}</span
                  >
                  <h2>{{ current.title }}</h2>
                </div>
                <el-tag type="info" effect="plain"
                  >单元 V{{ current.version.version_number }}</el-tag
                >
              </div>
              <pre class="lesson-text">{{ current.current_content }}</pre>
            </section>
            <section class="feedback-area">
              <div class="feedback-heading">
                <div>
                  <span class="eyebrow">A SECOND PERSPECTIVE</span>
                  <h2>修订建议</h2>
                </div>
                <el-tag v-if="current.review.generated_at" type="info"
                  >{{ current.suggestions.length }} 条建议</el-tag
                >
              </div>
              <div
                v-if="!current.review.generated_at"
                class="panel generate-box"
              >
                <h3>从这个单元开始思考</h3>
                <p class="muted">
                  结合课题与当前内容，生成 0–2 条可供你判断的建议。
                </p>
                <el-button
                  type="primary"
                  :loading="busy"
                  @click="mutate('/suggestions', target())"
                  >{{
                    provider === "mock" ? "生成模拟建议" : "生成 AI 建议"
                  }}</el-button
                >
              </div>
              <div
                v-else-if="!current.suggestions.length"
                class="panel empty-suggestions"
              >
                <h3>本单元暂无新增建议</h3>
                <p class="muted">可以保留当前内容，继续下一个单元。</p>
              </div>
              <article
                v-for="(suggestion, i) in current.suggestions"
                :key="suggestion.id"
                class="panel suggestion"
                :class="{ decided: suggestion.decision }"
              >
                <div class="suggestion-title">
                  <span class="eyebrow">SUGGESTION 0{{ i + 1 }}</span
                  ><el-tag
                    v-if="suggestion.decision"
                    :type="
                      suggestion.decision.decision === 'ACCEPT'
                        ? 'success'
                        : 'info'
                    "
                    >{{
                      suggestion.decision.decision === "ACCEPT"
                        ? "已采纳"
                        : "已拒绝"
                    }}</el-tag
                  >
                </div>
                <h3>{{ suggestion.issue }}</h3>
                <dl>
                  <dt>原因 / Reason</dt>
                  <dd>{{ suggestion.reason }}</dd>
                  <dt>
                    教学依据 / Pedagogical basis
                    <span class="basis-label">{{ suggestion.basis_type === 'verified_source' ? '引用已核验资料 · 以下解释由模型生成' : '暂定 · 未经知识库验证' }}</span>
                  </dt>
                  <dd>{{ suggestion.pedagogical_basis }}</dd>
                  <dd v-for="source in suggestion.basis_sources || []" :key="source.id" class="source-note">
                    <strong>来源：{{ source.source }} · {{ source.source_locator }}</strong><br>{{ source.content }}
                  </dd>
                  <dt>{{ suggestion.revision_mode === 'replace' ? '建议替换的正文' : '建议补充的正文' }} / Revision</dt>
                  <dd v-if="suggestion.target_text" class="muted">替换位置：{{ suggestion.target_text }}</dd>
                  <dd class="revision-text">{{ suggestion.revision }}</dd>
                </dl>
                <div v-if="!suggestion.decision" class="decision-actions">
                  <el-button
                    type="primary"
                    :disabled="busy"
                    @click="
                      mutate('/suggestions/' + suggestion.id + '/decision', {
                        decision: 'ACCEPT',
                      })
                    "
                    >Yes · 采纳</el-button
                  ><el-button
                    :disabled="busy"
                    @click="
                      mutate('/suggestions/' + suggestion.id + '/decision', {
                        decision: 'REJECT',
                      })
                    "
                    >No · 拒绝</el-button
                  ><span class="muted">{{ suggestion.revision_mode === 'replace' ? '仅在原文定位唯一时替换' : '采纳后将追加到本单元' }}</span>
                </div>
                <div v-if="state.revision_candidate?.suggestion_id === suggestion.id && !suggestion.decision" class="candidate-box">
                  <p>{{ state.revision_candidate.reason }}</p><pre class="lesson-text">{{ state.revision_candidate.candidate }}</pre>
                  <el-button :disabled="busy" @click="mutate('/suggestions/' + suggestion.id + '/decision', {decision:'ACCEPT', confirm_append:true, expected_version_id:state.revision_candidate.expected_version_id})">作为补充采纳（保留原文）</el-button>
                </div>
              </article>
            </section>
            <div class="inquiry-placeholder">
              <span>主动探询 / Custom prompt</span
              ><el-tag type="info" effect="plain">后续研究开放</el-tag
              ><el-input disabled placeholder="此演示版本暂未启用主动探询" />
            </div>
            <div class="next-bar">
              <p class="muted">
                {{
                  canNext
                    ? "本单元的决定已保存，可以继续。"
                    : "请生成建议，并对每条建议作出选择。"
                }}
              </p>
              <el-button
                type="primary"
                size="large"
                :disabled="!canNext || busy"
                @click="mutate('/complete-section', target())"
                >{{
                  current.order_index + 1 === state.sections.length
                    ? "完成本轮"
                    : "下一个单元"
                }}
                →</el-button
              >
            </div>
          </div>
        </div>
        <section
          v-if="status === 'ROUND_COMPLETED'"
          class="panel round-complete"
        >
          <span class="complete-icon">✓</span>
          <p class="eyebrow">ROUND {{ state.round.round_number }} COMPLETE</p>
          <h2>这一轮的改进，已完整保存。</h2>
          <p class="lead">
            {{ state.sections.length }} 个教学单元已完成审阅。<br />{{
              state.round.round_number < maxRounds
                ? "继续审阅最新版教案，或在这里结束本次修订。"
                : "已完成五轮修订，请结束会话并查看最终教案。"
            }}
          </p>
          <div class="round-actions">
            <el-button
              size="large"
              :disabled="busy"
              @click="mutate('/terminate')"
              >结束并查看最终教案</el-button
            ><el-button
              v-if="state.round.round_number < maxRounds"
              type="primary"
              size="large"
              :disabled="busy"
              @click="mutate('/rounds/' + state.round.id + '/continue')"
              >继续第 {{ state.round.round_number + 1 }} 轮 →</el-button
            >
          </div>
        </section>
        <template v-if="status === 'ROUND_COMPLETED' || status === 'TERMINATED'"
          ><section class="panel final-panel">
            <div class="panel-heading">
              <div>
                <span class="eyebrow">{{
                  status === "TERMINATED"
                    ? "SESSION COMPLETED"
                    : "SAVED LESSON PLAN"
                }}</span>
                <h2>{{ status === "TERMINATED" ? "最终教案" : "本轮教案" }}</h2>
              </div>
              <div class="toolbar">
                <el-select
                  v-model="selectedVersion"
                  placeholder="当前最新版"
                  aria-label="教案版本"
                  clearable
                  @clear="selectedVersion = null"
                  style="width: 180px"
                  ><el-option
                    v-for="version in state.lesson_plan_versions"
                    :key="version.id"
                    :value="version.round_number"
                    :label="
                      version.round_number === 0
                        ? 'V0 · 原始教案'
                        : 'V' +
                          version.round_number +
                          ' · 第 ' +
                          version.round_number +
                          ' 轮'
                    " /></el-select
                ><el-button @click="downloadText">下载教案</el-button>
              </div>
            </div>
            <pre class="lesson-text final-text">{{ finalContent }}</pre>
          </section>
          <div class="final-actions">
            <a :href="'/api' + path + '/export'" download
              >导出完整研究记录（JSON） ↗</a
            ><el-button v-if="status === 'TERMINATED'" @click="newLesson"
              >开始新的教案</el-button
            >
          </div></template
        >
        <p class="session-id">
          会话编号 {{ state.session.id }} · 每一次建议、决定与版本均保留
        </p>
      </template>
    </main>
    <footer>
      <span>PedagoLoop</span><span>A space for thoughtful teaching.</span
      ><span>内部试用 · Alpha</span>
    </footer>
  </div>
</template>
