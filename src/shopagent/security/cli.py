from __future__ import annotations

import argparse

from shopagent.security.tokens import issue_token
from shopagent.settings import Settings


def run() -> None:
    parser = argparse.ArgumentParser(description="Issue a short-lived ShopAgent access token")
    parser.add_argument("role", choices=("user", "admin", "service"))
    parser.add_argument("subject", help="Authenticated user or service identifier")
    parser.add_argument("--agent", help="Bound Agent identity for service tokens")
    parser.add_argument("--ttl", type=int, default=3600, help="Lifetime in seconds")
    args = parser.parse_args()
    settings = Settings()
    secret = settings.service_token if args.role == "service" else settings.user_token_secret
    print(
        issue_token(
            secret,
            subject=args.subject,
            role=args.role,
            agent=args.agent,
            ttl_seconds=args.ttl,
        )
    )


if __name__ == "__main__":
    run()
