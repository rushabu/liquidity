import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import App from "../App";
import * as client from "../api/client";
import { emptySummary } from "../test/fixtures";
import { getCurrentSession, setCurrentSession, type Session } from "./session";

const session = (role: "support" | "standard", location: string | null): Session => ({
  access_token: `token-${role}`, refresh_token: "r", expires_at: Math.floor(Date.now() / 1000) + 3600,
  user: { id: "u1", email: role === "support" ? "support@liquidplus.dev" : "singapore@liquidplus.dev", role, location,
          scope: role === "support" ? "All locations" : location, can_upload: role === "support" },
});

const json = (status: number, body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function stubPages() {
  vi.spyOn(client, "getSummary").mockResolvedValue(emptySummary());
  vi.spyOn(client, "getBatches").mockResolvedValue({ items: [] });
  vi.spyOn(client, "getErrors").mockResolvedValue({ total: 0, page: 1, page_size: 50, items: [], rule_codes: [] });
}

const show = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);

beforeEach(() => { setCurrentSession(null); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("sends signed-out users to the login page, whatever page they asked for", async () => {
  show("/ladder");
  expect(await screen.findByRole("heading", { name: "Sign in to Liquid+" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
});

it("signs in a standard user: shows role and location, no upload cards, and returns to the page asked for", async () => {
  stubPages();
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    expect(url).toMatch(/\/api\/auth\/login$/);
    expect(JSON.parse(String(init?.body))).toEqual({ email: "singapore@liquidplus.dev", password: "pw" });
    return json(200, session("standard", "Singapore"));
  });
  vi.stubGlobal("fetch", fetch);
  show("/upload");
  await userEvent.click(await screen.findByText("Predefined accounts"));
  await userEvent.click(screen.getByRole("button", { name: "singapore@liquidplus.dev" }));
  await userEvent.type(screen.getByLabelText("Password"), "pw");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
  expect(screen.getByText("Standard")).toBeInTheDocument();
  expect(screen.getByText("Singapore", { selector: ".scope" })).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("Uploading the books is done by a Support user");
  expect(getCurrentSession()?.access_token).toBe("token-standard");
});

it("shows a wrong-password error and clears the password", async () => {
  vi.stubGlobal("fetch", vi.fn(() => json(401, { error: { code: "INVALID_CREDENTIALS", message: "Wrong email or password." } })));
  show("/login");
  await userEvent.type(await screen.findByLabelText("Email"), "support@liquidplus.dev");
  await userEvent.type(screen.getByLabelText("Password"), "bad");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
  expect(screen.getByLabelText("Password")).toHaveValue("");
  expect(getCurrentSession()).toBeNull();
});

it("support users see the upload cards; sign out wipes the session and remembered data", async () => {
  stubPages();
  setCurrentSession(session("support", null));
  const clear = vi.spyOn(client, "clearAiCache");
  show("/upload");
  expect(await screen.findByText("All locations")).toBeInTheDocument();
  expect(screen.queryByRole("note")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByRole("heading", { name: "Sign in to Liquid+" })).toBeInTheDocument();
  expect(getCurrentSession()).toBeNull();
  expect(clear).toHaveBeenCalled();
});

it("a viewer sees all locations but gets the read-only note instead of upload cards", async () => {
  stubPages();
  setCurrentSession({ ...session("support", null), user: { id: "v", email: "innoversite@liquidplus.dev", role: "viewer", location: null, scope: "All locations", can_upload: false } });
  show("/upload");
  expect(await screen.findByText("Viewer", { selector: ".role-chip" })).toBeInTheDocument();
  expect(screen.getByText("All locations", { selector: ".scope" })).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("view every location on every page (read-only)");
});

it("an expired token (401 from the API) signs the user out with a notice", async () => {
  setCurrentSession(session("support", null));
  vi.stubGlobal("fetch", vi.fn(() => json(401, { error: { code: "UNAUTHORIZED", message: "Invalid token: Signature has expired" } })));
  show("/upload");
  expect(await screen.findByRole("heading", { name: "Sign in to Liquid+" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Your session has expired"));
});
