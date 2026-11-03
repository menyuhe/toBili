// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { calcJobProgressPercent, refreshAllDataSourceCount, refreshAllPipelinePhase, participateProgressLabels, participateActiveStepIndex } from "./index";

describe("single-source progress", () => {
  it("shows the real repost lookup between repost and comment", () => {
    const labels = participateProgressLabels(6);
    expect(labels).toEqual(["点赞", "关注", "收藏", "转发", "获取本人转发", "评论"]);
    expect(participateActiveStepIndex("正在获取本人转发（5/6）", 6, labels)).toBe(4);
    expect(participateProgressLabels(2)).toEqual(["关注", "预约"]);
  });
  it("shows classification as the second phase at 8/15", () => {
    const job = { action: "refresh_source", progress_step: 2, progress_total: 4, progress_message: "分类进度 (8/15)" };
    const count = refreshAllDataSourceCount(job);
    expect(count).toBe(1);
    expect(refreshAllPipelinePhase(2, count, job.progress_message)).toBe(1);
    expect(calcJobProgressPercent(job)).toBe(60);
  });

  it("keeps one source before a progress total arrives", () => {
    expect(refreshAllDataSourceCount({ action: "refresh_source", progress_total: 0 })).toBe(1);
    expect(refreshAllDataSourceCount({ action: "refresh_all", progress_total: 10 })).toBe(7);
  });
});
