import Link from "next/link";
import { AppFrame } from "@/components/AppFrame";
import { requireUser } from "@/lib/auth";
import "../styles/app.css";

/** Every signed-in page lives under here, so one check protects them all and the shell is drawn once. */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  const env = process.env.VERCEL_ENV === "production" ? null : (process.env.VERCEL_ENV ?? "local");
  return (
    <AppFrame
      email={user.email}
      admin={user.role === "admin"}
      env={env}
      banner={
        user.verified ? null : (
          <div role="status" className="banner">
            Confirm your email address to create API keys. <Link href="/account">Open your account</Link>
          </div>
        )
      }
    >
      {children}
    </AppFrame>
  );
}
