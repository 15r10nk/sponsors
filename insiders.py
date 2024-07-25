from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime

import httpx

# permissions: admin:org and read:user
GITHUB_TOKEN = os.getenv("TOKEN")
SPONSORED_ACCOUNT = "15r10nk"
MIN_AMOUNT = 4
INSIDERS_TEAMS = [
    ("15r10nk-insiders", "insiders"),
]
PRIVILEGED_USERS = frozenset(
    {
        "15r10nk",  # Myself.
    }
)
ORG_USERS = {
}

GRAPHQL_SPONSORS = """
query {
    viewer {
        sponsorshipsAsMaintainer(
        first: 100,
        after: %s
        includePrivate: true,
        orderBy: {
            field: CREATED_AT,
            direction: DESC
        }
        ) {
        pageInfo {
            hasNextPage
            endCursor
        }
        nodes {
            createdAt,
            isOneTimePayment,
            privacyLevel,
            sponsorEntity {
            ...on Actor {
                __typename,
                login,
                avatarUrl,
                url
            }
            },
            tier {
            monthlyPriceInDollars
            }
        }
        }
    }
}
"""


@dataclass
class Account:
    name: str
    image: str
    url: str
    org: bool

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "image": self.image,
            "url": self.url,
            "org": self.org,
        }


@dataclass
class Sponsor:
    account: Account
    private: bool
    created: datetime
    amount: int


def get_sponsors() -> list[Sponsor]:
    sponsors = []
    with httpx.Client(base_url="https://api.github.com") as client:
        cursor = "null"
        while True:
            # Get sponsors data
            payload = {"query": GRAPHQL_SPONSORS % cursor}
            response = client.post(
                f"https://api.github.com/graphql",
                json=payload,
                headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            )
            response.raise_for_status()

            # Post-process sponsors data
            data = response.json()["data"]
            for item in data["viewer"]["sponsorshipsAsMaintainer"]["nodes"]:
                if item["isOneTimePayment"]:
                    continue

                # Determine account
                account = Account(
                    name=item["sponsorEntity"]["login"],
                    image=item["sponsorEntity"]["avatarUrl"],
                    url=item["sponsorEntity"]["url"],
                    org=item["sponsorEntity"]["__typename"].lower() == "organization",
                )

                # Add sponsor
                sponsors.append(
                    Sponsor(
                        account=account,
                        private=item["privacyLevel"].lower() == "private",
                        created=datetime.strptime(item["createdAt"], "%Y-%m-%dT%H:%M:%SZ"),
                        amount=item["tier"]["monthlyPriceInDollars"],
                    )
                )

            # Check for next page
            if data["viewer"]["sponsorshipsAsMaintainer"]["pageInfo"]["hasNextPage"]:
                cursor = f'"{data["viewer"]["sponsorshipsAsMaintainer"]["pageInfo"]["endCursor"]}"'
            else:
                break

    return sponsors


def get_members(org: str, team: str) -> set[str]:
    page = 1
    members = set()
    while True:
        response = httpx.get(
            f"https://api.github.com/orgs/{org}/teams/{team}/members",
            params={"per_page": 100, "page": page},
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        )
        response.raise_for_status()
        response_data = response.json()
        members |= {member["login"] for member in response_data}
        if len(response_data) < 100:
            break
        page += 1
    return {user["login"] for user in response.json()}


def get_invited(org: str, team: str) -> set[str]:
    response = httpx.get(
        f"https://api.github.com/orgs/{org}/teams/{team}/invitations",
        params={"per_page": 100},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
    )
    response.raise_for_status()
    return {user["login"] for user in response.json()}


def grant(user: str, org: str, team: str):
    with httpx.Client() as client:
        response = client.put(
            f"https://api.github.com/orgs/{org}/teams/{team}/memberships/{user}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            print(f"Couldn't add @{user} to {org}/{team} team: {error}")
            if response.content:
                response_body = response.json()
                print(f"{response_body['message']} See {response_body['documentation_url']}")
        else:
            print(f"@{user} added to {org}/{team} team")


def revoke(user: str, org: str, team: str):
    with httpx.Client() as client:
        response = client.delete(
            f"https://api.github.com/orgs/{org}/teams/{team}/memberships/{user}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            print(f"Couldn't remove @{user} from {org}/{team} team: {error}")
            if response.content:
                response_body = response.json()
                print(f"{response_body['message']} See {response_body['documentation_url']}")
        else:
            print(f"@{user} removed from {org}/{team} team")


def main():
    sponsors = get_sponsors()

    eligible_orgs = {sponsor.account.name for sponsor in sponsors if sponsor.account.org and sponsor.amount >= MIN_AMOUNT}
    eligible_users = {sponsor.account.name for sponsor in sponsors if not sponsor.account.org and sponsor.amount >= MIN_AMOUNT}
    eligible_users |= PRIVILEGED_USERS
    for eligible_org in eligible_orgs:
        eligible_users |= ORG_USERS.get(eligible_org, set())

    for org, team in INSIDERS_TEAMS:
        members = get_members(org, team) | get_invited(org, team)
        # revoke accesses
        for user in members:
            if user not in eligible_users:
                revoke(user, org, team)
        # grant accesses
        for user in eligible_users:
            if user not in members:
                grant(user, org, team)

    total = sum(sponsor.amount for sponsor in sponsors)
    count = len(sponsors)
    with open("numbers.json", "w") as file:
        json.dump({"total": total, "count": count}, file)
    with open("sponsors.json", "w") as file:
        json.dump([sponsor.account.as_dict() for sponsor in sponsors if not sponsor.private], file, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
