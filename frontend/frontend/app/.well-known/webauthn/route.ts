import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json(
    {
      provider: "DevAnalysis114 Admin",
      passkey: true,
      rpId: "metanova1004.com",
      origins: ["https://metanova1004.com"],
    },
    {
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}
