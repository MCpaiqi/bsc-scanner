import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { LivePerformance } from "./LivePerformance";
import type { LivePerformance as PerformanceData } from "../types/radar";

const empty = { sample_count: 0, median_return: null, win_rate: null, "2x_rate": null, rug_rate: null, max_drawdown: null };
it.each(["REBUILDING", "UNKNOWN", "UNAVAILABLE"])("does not present %s statistics as a measured empty cohort", status => {
  const data = { mode: "FORWARD_OBSERVATION", data_status: status, withheld_count: 20,
    horizons: { "5m": { HIGH_SIGNAL: empty } } } satisfies PerformanceData;
  const html = renderToStaticMarkup(<LivePerformance data={data} />);
  expect(html).not.toContain("统计已读取");
  expect(html).not.toContain("0<span>个");
  expect(html).toContain(status === "REBUILDING" ? "重建中" : "暂不可用");
});
it("preserves a genuinely current zero-sample cohort", () => {
  const data = { mode: "FORWARD_OBSERVATION", data_status: "CURRENT", withheld_count: 0,
    horizons: { "5m": { HIGH_SIGNAL: empty } } } satisfies PerformanceData;
  const html = renderToStaticMarkup(<LivePerformance data={data} />);
  expect(html).toContain("统计已读取");
  expect(html).toContain("0<span>个");
});
