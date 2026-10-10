import { asOfFromFilename, isValidIsoDate } from "./dates";
import { checkFile } from "./files";
import { formatBytes, formatDuration, formatInt } from "./format";
import { file } from "../test/fixtures";

describe("asOfFromFilename", () => {
  it.each([
    ["asset_book - 10102026.xlsx", "2026-10-10"],
    ["liability_book_-_01022027.csv", "2027-02-01"],
    ["x 31122025 y.xlsx", "2025-12-31"],
  ])("reads DDMMYYYY from %s", (name, iso) => expect(asOfFromFilename(name)).toBe(iso));

  it.each(["asset.xlsx", "asset_32132026.xlsx", "asset_101020261.xlsx", "29022027.xlsx"])("returns null for %s", (n) =>
    expect(asOfFromFilename(n)).toBeNull());
});

describe("isValidIsoDate", () => {
  it("accepts real dates incl. leap day", () => {
    expect(isValidIsoDate("2026-10-10")).toBe(true);
    expect(isValidIsoDate("2028-02-29")).toBe(true);
  });
  it.each(["2026-02-30", "2027-02-29", "10-10-2026", "2026-1-1", "", "2026-13-01"])("rejects %s", (s) =>
    expect(isValidIsoDate(s)).toBe(false));
});

describe("checkFile (extension and size only)", () => {
  it("accepts xlsx and csv, case-insensitive", () => {
    expect(checkFile(file("a.xlsx"), 25)).toBeNull();
    expect(checkFile(file("A.CSV"), 25)).toBeNull();
  });
  it("rejects other types, empty and oversized files", () => {
    expect(checkFile(file("a.pdf"), 25)).toMatch(/Unsupported/);
    expect(checkFile(file("a.xlsx", 0), 25)).toMatch(/empty/);
    expect(checkFile(file("a.xlsx", 2 * 1024 * 1024 + 1), 2)).toMatch(/larger than 2 MB/);
  });
});

describe("format", () => {
  it("formats numbers, bytes and durations", () => {
    expect(formatInt(16179)).toBe("16,179");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatDuration(151)).toBe("151 ms");
    expect(formatDuration(3034)).toBe("3.0 s");
  });
});
