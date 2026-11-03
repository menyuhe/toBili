// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { fetchJSON } from "../api/client";
import { confirmRefreshAll } from "./confirm";

vi.mock("../api/client", () => ({ fetchJSON: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

it("reads the current catalog each time the confirmation opens", async () => {
  const prompt = vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.mocked(fetchJSON).mockResolvedValueOnce({ items: [...Array.from({length:8}, () => ({backup:false})), {backup:true}, {backup:true}] });
  await confirmRefreshAll();
  expect(prompt).toHaveBeenLastCalledWith("确认一键更新 8 个常用数据源？");
  vi.mocked(fetchJSON).mockResolvedValueOnce({items:[{backup:false}, {backup:true}]});
  await confirmRefreshAll();
  expect(prompt).toHaveBeenLastCalledWith("确认一键更新 1 个常用数据源？");
});

it("does not start an update when all sources are manual", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(fetchJSON).mockResolvedValueOnce({items:[{backup:true}]});
  expect(await confirmRefreshAll()).toBe(false);
});
