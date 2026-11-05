import "server-only";
import { NextResponse } from "next/server";
import { ZodError, type ZodTypeAny, type output } from "zod";
import { currentHousehold } from "@/lib/session";

export type Ctx = NonNullable<Awaited<ReturnType<typeof currentHousehold>>>;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

export const notFound = () => new ApiError(404, "not_found");

/**
 * Wrap a route handler: requires a signed-in user with a household (spec §10: every route checks
 * auth and household membership), turns Zod errors into 400s and ApiErrors into their status.
 */
export function withHousehold<P = Record<string, string>>(
  fn: (req: Request, ctx: Ctx, params: P) => Promise<unknown>,
) {
  return async (req: Request, route: { params: Promise<P> }) => {
    try {
      const ctx = await currentHousehold();
      if (!ctx) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
      const result = await fn(req, ctx, await route.params);
      if (result instanceof Response) return result;
      return NextResponse.json(result ?? { ok: true });
    } catch (e) {
      if (e instanceof ZodError)
        return NextResponse.json({ error: "invalid_input", issues: e.issues }, { status: 400 });
      if (e instanceof ApiError)
        return NextResponse.json({ error: e.message, details: e.details }, { status: e.status });
      console.error(e);
      return NextResponse.json({ error: "server_error" }, { status: 500 });
    }
  };
}

export async function parseJson<S extends ZodTypeAny>(req: Request, schema: S): Promise<output<S>> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    throw new ApiError(400, "invalid_json");
  }
  return schema.parse(body);
}

export const isoDate = /^\d{4}-\d{2}-\d{2}$/;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function assertUuid(id: string) {
  if (!UUID.test(id)) throw notFound();
}
