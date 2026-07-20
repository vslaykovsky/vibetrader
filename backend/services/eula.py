from __future__ import annotations

import ipaddress
import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.supabase_trading_settings import _get, _patch, _post, service_role_configured


logger = logging.getLogger(__name__)

EULA_VERSION = "2026-07-19"
EULA_EFFECTIVE_DATE = "July 19, 2026"
EULA_CONTACT_EMAIL = "legal@traderchat.ai"
EULA_OPERATOR_ADDRESS = "844 Enchanted Way, Pacific Palisades, CA 90272, USA"
_CACHE_SECONDS = 60
_acceptance_cache: dict[tuple[str, str], tuple[float, bool]] = {}
_cache_lock = threading.Lock()


EULA_DOCUMENT: dict[str, Any] = {
    "title": "TraderChat.ai End User License Agreement",
    "version": EULA_VERSION,
    "effective_date": EULA_EFFECTIVE_DATE,
    "summary": (
        "This agreement governs access to TraderChat.ai, including research, backtesting, "
        "strategy generation, and user-enabled live order execution. Read it carefully before "
        "using the service."
    ),
    "sections": [
        {
            "title": "1. Agreement and operator",
            "paragraphs": [
                (
                    "This End User License Agreement (the \"Agreement\") is a binding agreement "
                    "between you and the operator of TraderChat.ai (\"TraderChat,\" \"we,\" "
                    "\"us,\" or \"our\"), located at 844 Enchanted Way, Pacific Palisades, "
                    "California 90272, USA. By selecting the acceptance checkbox and continuing, "
                    "you confirm that you have read, understood, and agree to this Agreement."
                ),
            ],
        },
        {
            "title": "2. Eligibility and territory",
            "paragraphs": [
                (
                    "You must be at least 18 years old, have legal capacity to enter this "
                    "Agreement, and use TraderChat only where its use and any resulting trading "
                    "activity are lawful. If you use TraderChat for an organization, you represent "
                    "that you are authorized to bind it. You may not use the service if you are "
                    "subject to applicable trade sanctions or other legal restrictions."
                ),
                (
                    "You are responsible for accurate account information, the security of your "
                    "login and brokerage credentials, and all activity performed through your "
                    "account. Notify us promptly if you suspect unauthorized access."
                ),
            ],
        },
        {
            "title": "3. Software license and service",
            "paragraphs": [
                (
                    "Subject to this Agreement, TraderChat grants you a limited, personal, "
                    "revocable, non-exclusive, non-transferable license to access and use the "
                    "service for lawful research, testing, and trading workflows. TraderChat is "
                    "currently offered without a subscription fee and may be changed, suspended, "
                    "or discontinued at any time. Third parties, including brokers and data "
                    "providers, may charge their own fees."
                ),
                (
                    "You may not copy or resell the service, bypass access controls, interfere with "
                    "its operation, probe it for vulnerabilities without authorization, use it to "
                    "violate market rules or law, or use it to place trades for an account you are "
                    "not authorized to control."
                ),
            ],
        },
        {
            "title": "4. Software tool; no financial advice",
            "paragraphs": [
                (
                    "TraderChat is general-purpose software for generating and editing strategy "
                    "code, analyzing market data, running simulations and backtests, monitoring "
                    "strategies, and transmitting user-configured orders. Its output is generated "
                    "from your instructions, software models, algorithms, and third-party data. It "
                    "does not evaluate your complete financial circumstances, objectives, tax "
                    "position, other holdings, liquidity needs, or suitability requirements."
                ),
                (
                    "TraderChat does not provide individualized investment, legal, tax, or "
                    "accounting advice and does not recommend that any security, digital asset, "
                    "strategy, or transaction is suitable for you. Nothing in the service is an "
                    "offer, solicitation, guarantee, or promise of results. TraderChat is not your "
                    "broker, exchange, custodian, fiduciary, or investment adviser, and no such "
                    "relationship is created by this Agreement. You must make your own decisions "
                    "and obtain advice from appropriately licensed professionals when needed."
                ),
            ],
        },
        {
            "title": "5. Live trading and user authorization",
            "paragraphs": [
                (
                    "If you connect a brokerage account and enable live execution, TraderChat may "
                    "send orders generated by your selected strategy and settings to that account "
                    "automatically, without asking you to approve each order. By enabling a live "
                    "strategy, you expressly authorize those transmissions until you stop or "
                    "disable the strategy or disconnect the account."
                ),
                (
                    "You remain solely responsible for deciding whether to enable live execution; "
                    "reviewing strategy code, instruments, parameters, position sizing, leverage, "
                    "sessions, and risk controls; maintaining sufficient funds and permissions; "
                    "monitoring orders, fills, positions, and account activity; and stopping a "
                    "strategy when appropriate. A stop request may not cancel orders already sent "
                    "or filled."
                ),
                (
                    "Orders are received and executed by your broker under your separate brokerage "
                    "agreement. A broker may reject, delay, modify, route, or cancel orders. "
                    "TraderChat does not hold customer funds or securities and does not control "
                    "execution quality, market availability, broker systems, or settlement."
                ),
            ],
        },
        {
            "title": "6. Trading, model, and technology risks",
            "paragraphs": [
                "You understand and accept that using TraderChat can cause substantial or total financial loss.",
            ],
            "bullets": [
                (
                    "Markets are volatile. Stocks, digital assets, short positions, leverage, thinly "
                    "traded instruments, and after-hours trading may produce rapid losses exceeding "
                    "expectations or available cash."
                ),
                (
                    "Generated code and AI output may be incorrect, incomplete, insecure, "
                    "misleading, or inconsistent with your request. You must inspect and test it."
                ),
                (
                    "Backtests and simulated results are hypothetical. They may contain selection "
                    "bias, overfitting, look-ahead bias, survivorship bias, data errors, unrealistic "
                    "fills, or assumptions about liquidity, spreads, slippage, fees, taxes, corporate "
                    "actions, and market impact. Past or simulated performance does not predict "
                    "future results."
                ),
                (
                    "Software defects, stale or missing data, clock and session errors, network "
                    "failures, latency, duplicate or missed events, broker outages, credential "
                    "compromise, and other failures may create unintended, delayed, duplicated, or "
                    "unexecuted orders."
                ),
                (
                    "Risk limits, alerts, stop controls, and monitoring may fail or act later than "
                    "expected. You must maintain independent safeguards appropriate to your account."
                ),
            ],
        },
        {
            "title": "7. Third-party services and data",
            "paragraphs": [
                (
                    "TraderChat depends on services supplied by third parties, which may include "
                    "Alpaca, market-data vendors, exchanges, cloud providers, and AI model providers. "
                    "Your use of those services is governed by their own agreements and disclosures. "
                    "TraderChat is not responsible for their availability, accuracy, security, "
                    "decisions, fees, or conduct. References to a third party do not imply that it "
                    "endorses or guarantees TraderChat."
                ),
            ],
        },
        {
            "title": "8. Your content, strategies, and feedback",
            "paragraphs": [
                (
                    "As between you and TraderChat, you retain your rights in prompts, strategy "
                    "logic, code, and other material you submit. You grant TraderChat a worldwide, "
                    "non-exclusive license to host, copy, execute, transmit, and process that "
                    "material only as reasonably necessary to operate, secure, support, and improve "
                    "the service. You represent that you have the necessary rights to submit it."
                ),
                (
                    "If you provide feedback, you permit TraderChat to use it without restriction or "
                    "compensation. TraderChat and its licensors retain all rights in the service, "
                    "software, branding, documentation, and underlying technology."
                ),
            ],
        },
        {
            "title": "9. Data and security",
            "paragraphs": [
                (
                    "TraderChat processes account identifiers, prompts, strategy code, execution "
                    "settings, brokerage API credentials, trading activity, and technical logs as "
                    "needed to provide and secure the service. No security measure is infallible. "
                    "You should use broker permissions and credentials that limit access to what is "
                    "necessary, protect your devices, and promptly rotate compromised credentials."
                ),
            ],
        },
        {
            "title": "10. Beta service; no warranties",
            "paragraphs": [
                (
                    "TO THE MAXIMUM EXTENT PERMITTED BY LAW, TRADERCHAT IS PROVIDED \"AS IS\" AND "
                    "\"AS AVAILABLE.\" TRADERCHAT DISCLAIMS ALL EXPRESS, IMPLIED, AND STATUTORY "
                    "WARRANTIES, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, "
                    "TITLE, NON-INFRINGEMENT, ACCURACY, AVAILABILITY, SECURITY, AND ANY WARRANTY "
                    "THAT THE SERVICE WILL BE ERROR-FREE OR PRODUCE PROFITS."
                ),
            ],
        },
        {
            "title": "11. Limitation of liability",
            "paragraphs": [
                (
                    "TO THE MAXIMUM EXTENT PERMITTED BY LAW, TRADERCHAT AND ITS OPERATORS, "
                    "AFFILIATES, LICENSORS, AND SERVICE PROVIDERS WILL NOT BE LIABLE FOR INDIRECT, "
                    "INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES; LOST "
                    "PROFITS, DATA, OPPORTUNITIES, OR GOODWILL; OR TRADING LOSSES, BROKER ACTIONS, "
                    "OR UNAUTHORIZED TRANSACTIONS, EVEN IF ADVISED THAT SUCH LOSS IS POSSIBLE."
                ),
                (
                    "TO THE MAXIMUM EXTENT PERMITTED BY LAW, THEIR TOTAL AGGREGATE LIABILITY "
                    "ARISING OUT OF OR RELATING TO THE SERVICE OR THIS AGREEMENT WILL NOT EXCEED "
                    "THE GREATER OF ONE HUNDRED U.S. DOLLARS (US $100) OR THE AMOUNT YOU PAID "
                    "TRADERCHAT FOR THE SERVICE DURING THE 12 MONTHS BEFORE THE EVENT GIVING RISE "
                    "TO THE CLAIM. Some jurisdictions do not permit certain exclusions or limits, "
                    "so parts of this section may not apply to you."
                ),
            ],
        },
        {
            "title": "12. Indemnity",
            "paragraphs": [
                (
                    "To the extent permitted by law, you will defend, indemnify, and hold harmless "
                    "TraderChat and its operators, affiliates, licensors, and service providers from "
                    "third-party claims and reasonable costs arising from your unlawful use of the "
                    "service, breach of this Agreement, infringement of another person's rights, or "
                    "trading activity in an account you were not authorized to control."
                ),
            ],
        },
        {
            "title": "13. Suspension and termination",
            "paragraphs": [
                (
                    "You may stop using TraderChat at any time. We may suspend or terminate access "
                    "to protect users or systems, comply with law, respond to third-party action, or "
                    "address a breach or material risk. Termination does not automatically cancel "
                    "open broker orders or close positions; you remain responsible for your "
                    "brokerage account. Provisions that by their nature should survive termination "
                    "will survive."
                ),
            ],
        },
        {
            "title": "14. Governing law and disputes",
            "paragraphs": [
                (
                    "This Agreement is governed by California law, without regard to conflict-of-law "
                    "principles. Subject to any non-waivable consumer rights, the state and federal "
                    "courts located in Los Angeles County, California have exclusive jurisdiction, "
                    "and you and TraderChat consent to personal jurisdiction and venue there. Before "
                    "filing a claim, each party agrees to give the other written notice and 30 days "
                    "to attempt an informal resolution."
                ),
            ],
        },
        {
            "title": "15. Changes and electronic acceptance",
            "paragraphs": [
                (
                    "We may update this Agreement as the service or law changes. If an update is "
                    "material, we will present the new version for acceptance before further use. "
                    "Your electronic acceptance has the same effect as a handwritten signature. We "
                    "may retain the accepted version, time, IP address, and user-agent information "
                    "as evidence of acceptance."
                ),
                (
                    "This Agreement is the entire agreement about the licensed service unless a "
                    "separate written agreement applies. If a provision is unenforceable, it will be "
                    "limited to the minimum extent necessary and the remaining provisions will "
                    "continue. Failure to enforce a provision is not a waiver. You may not assign "
                    "this Agreement without our consent; TraderChat may assign it with the service "
                    "or its business."
                ),
            ],
        },
        {
            "title": "16. Contact",
            "paragraphs": [
                (
                    "Questions or legal notices may be sent to legal@traderchat.ai or to "
                    "TraderChat.ai, 844 Enchanted Way, Pacific Palisades, CA 90272, USA."
                ),
            ],
        },
    ],
}


@dataclass(frozen=True)
class EulaAcceptance:
    accepted: bool
    accepted_version: str
    accepted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "current_version": EULA_VERSION,
            "accepted_version": self.accepted_version,
            "accepted_at": self.accepted_at,
        }


def _cache_get(user_id: str) -> bool | None:
    key = (user_id, EULA_VERSION)
    with _cache_lock:
        cached = _acceptance_cache.get(key)
        if cached is None:
            return None
        expires_at, accepted = cached
        if expires_at <= time.monotonic():
            _acceptance_cache.pop(key, None)
            return None
        return accepted


def _cache_set(user_id: str, accepted: bool) -> None:
    with _cache_lock:
        _acceptance_cache[(user_id, EULA_VERSION)] = (
            time.monotonic() + _CACHE_SECONDS,
            accepted,
        )


def clear_eula_acceptance_cache() -> None:
    with _cache_lock:
        _acceptance_cache.clear()


def fetch_eula_acceptance(user_id: str) -> EulaAcceptance | None:
    uid = str(user_id or "").strip()
    if not uid or not service_role_configured():
        return None
    response = _get(
        "profiles",
        {
            "id": f"eq.{urllib.parse.quote(uid, safe='')}",
            "select": "eula_accepted,eula_version,eula_accepted_at",
        },
    )
    if response.status_code != 200:
        logger.warning("supabase EULA status fetch status=%s", response.status_code)
        return None
    rows = response.json()
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return EulaAcceptance(False, "", None)
    row = rows[0]
    accepted_version = str(row.get("eula_version") or "").strip()
    accepted = bool(row.get("eula_accepted")) and accepted_version == EULA_VERSION
    accepted_at = row.get("eula_accepted_at")
    return EulaAcceptance(
        accepted=accepted,
        accepted_version=accepted_version,
        accepted_at=str(accepted_at) if accepted_at else None,
    )


def is_current_eula_accepted(user_id: str) -> bool | None:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    cached = _cache_get(uid)
    if cached is not None:
        return cached
    acceptance = fetch_eula_acceptance(uid)
    if acceptance is None:
        return None
    _cache_set(uid, acceptance.accepted)
    return acceptance.accepted


def normalize_client_ip(value: str | None) -> str | None:
    candidate = str(value or "").split(",", 1)[0].strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def record_eula_acceptance(
    user_id: str,
    *,
    email: str | None,
    client_ip: str | None,
    user_agent: str | None,
) -> EulaAcceptance | None:
    uid = str(user_id or "").strip()
    if not uid or not service_role_configured():
        return None
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "eula_accepted": True,
        "eula_version": EULA_VERSION,
        "eula_accepted_at": now,
        "eula_accepted_ip": normalize_client_ip(client_ip),
        "eula_accepted_user_agent": str(user_agent or "")[:1024],
        "updated_at": now,
    }
    encoded_uid = urllib.parse.quote(uid, safe="")
    existing = _get("profiles", {"id": f"eq.{encoded_uid}", "select": "id"})
    if existing.status_code != 200:
        logger.warning("supabase EULA profile lookup status=%s", existing.status_code)
        return None
    rows = existing.json()
    if isinstance(rows, list) and rows:
        response = _patch(f"profiles?id=eq.{encoded_uid}", body)
        ok = response.status_code in (200, 204)
    else:
        response = _post(
            "profiles",
            {
                "id": uid,
                "email": str(email or "").strip(),
                **body,
            },
        )
        ok = response.status_code in (200, 201)
    if not ok:
        logger.warning("supabase EULA acceptance write status=%s", response.status_code)
        return None
    acceptance = EulaAcceptance(True, EULA_VERSION, now)
    _cache_set(uid, True)
    return acceptance
