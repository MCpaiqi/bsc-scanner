import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EventStream } from "./EventStream";

describe("EventStream", () => {
  it("renders live event labels and token identity", () => {
    const html = renderToStaticMarkup(<EventStream connected events={[{ event_id: "1", event_type: "SMART_MONEY_ENTRY", chain: "bsc", token: "0xabcdef123456", occurred_at: new Date().toISOString(), data: {} }]} />);
    expect(html).toContain("聪明钱入场");
    expect(html).toContain("0xabcd...3456");
    expect(html).toContain("已连接");
  });
});
