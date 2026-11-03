// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
vi.mock("../api/client", () => ({ fetchJSON: vi.fn() }));
vi.mock("../shell/toast", () => ({ showToast: vi.fn() }));
import { fetchJSON } from "../api/client";
import { bindJobHistory, renderJobLog, loadJobHistory } from "./history";

beforeEach(() => {
  document.body.innerHTML = `<select id="job-history-select"><option value="">当前</option><option value="1">旧任务</option></select>
  <div id="log-dock"></div><span id="log-dock-status"></span><p id="job-message"></p><p id="log-dock-summary"></p><pre id="job-log"></pre><p id="job-history-meta"></p><button id="job-log-live"></button><button id="job-history-more"></button>`;
  bindJobHistory();
  document.getElementById("job-log-live")!.click();
});

it("keeps history readable while the next job reports progress", async () => {
  vi.mocked(fetchJSON).mockResolvedValue({ id:1, state:"success", message:"旧任务已完成", log:"原始过程\n最终结果", label:"旧任务" });
  const select = document.getElementById("job-history-select") as HTMLSelectElement;
  select.value = "1";
  select.dispatchEvent(new Event("change"));
  await vi.waitFor(() => expect(document.getElementById("job-log")!.textContent).toContain("原始过程"));
  renderJobLog({ id:2, state:"running", message:"新任务连接失败", log:"新任务进度" });
  expect(document.getElementById("job-log")!.textContent).toBe("原始过程\n最终结果");
  expect(document.getElementById("job-message")!.textContent).toBe("旧任务已完成");
  expect(document.getElementById("log-dock")!.dataset.tone).toBe("success");
  document.getElementById("job-log-live")!.click();
  expect(document.getElementById("job-log")!.textContent).toBe("新任务进度");
  expect(document.getElementById("log-dock-summary")!.textContent).toBe("新任务连接失败");
  expect(document.getElementById("log-dock-status")!.textContent).toBe("运行中");
});

it("does not erase a completed log on an idle snapshot", () => {
  renderJobLog({ id:3, state:"success", log:"完成记录" });
  renderJobLog({ state:"idle", log:"" });
  expect(document.getElementById("job-log")!.textContent).toBe("完成记录");
});

it("loads historical jobs with text-safe labels", async () => {
  vi.mocked(fetchJSON).mockResolvedValue({ items:[{id:4,state:"success",label:"<img src=x>",started_at:1}], next_before_id:4 });
  await loadJobHistory();
  expect(document.querySelector("#job-history-select img")).toBeNull();
  expect(document.getElementById("job-history-more")!.hidden).toBe(false);
});
