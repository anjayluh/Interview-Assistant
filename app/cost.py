"""Offline estimates and user-confirmed, non-billable account admission."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DEFAULT_MINUTES = 5
CHANNELS = 2
PRICING_SOURCE = 'https://www.assemblyai.com/pricing'
GROQ_SOURCE = 'https://console.groq.com/docs/models'
PRICING_DATE = date(2026, 11, 19)
PRICE_MAX_AGE_DAYS = 30
FX_MAX_AGE_DAYS = 7
MARGIN = Decimal('1.25')
SHUTDOWN_RESERVE_SECONDS = 30
MAX_INPUT_TOKENS = 32768
MAX_OUTPUT_TOKENS = 2000
GROQ_PRICES = {
    'openai/gpt-oss-20b': (Decimal('0.075'), Decimal('0.30')),
    'openai/gpt-oss-120b': (Decimal('0.15'), Decimal('0.60')),
}


def ceiling(value):
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class FreeCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    assembly_credits_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    assembly_hourly_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=6)
    assembly_keyterms_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    assembly_plan: Literal['unknown', 'unupgraded_trial', 'billable'] = 'unknown'
    assembly_no_paid_fallback: StrictBool = False
    assembly_source: str = Field(default='', max_length=240)
    groq_plan: Literal['unknown', 'free', 'promotional_billable', 'paid'] = 'unknown'
    groq_no_paid_fallback: StrictBool = False
    groq_remaining_requests: int | None = Field(default=None, strict=True, ge=0, le=1000000000)
    groq_remaining_tokens: int | None = Field(default=None, strict=True, ge=0, le=1000000000)
    groq_source: str = Field(default='', max_length=240)
    checked_at: datetime | None = None
    valid_until: datetime | None = None
    exclusive_balances: StrictBool = False


class LiveOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    minutes: int = Field(default=DEFAULT_MINUTES, strict=True, ge=1, le=1440)
    cap_ugx: int = Field(default=0, strict=True, ge=0, le=1000000000)
    # Retained for optional gross-cost conversion. Never required for free-only admission.
    fx_ugx_per_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    fx_date: date | None = None
    fx_source: str = Field(default='', max_length=180)
    assembly_billing_checked: StrictBool = False
    groq_billing_checked: StrictBool = False
    confirmed: StrictBool = False
    max_answers: int = Field(default=3, strict=True, ge=1, le=100)
    free: FreeCoverage = Field(default_factory=FreeCoverage)


@dataclass(frozen=True)
class Quote:
    options: LiveOptions
    keyterms: bool
    max_minutes: float
    today: date
    model: str = 'openai/gpt-oss-20b'

    @property
    def channel_rate(self):
        allowance = self.options.free
        base = allowance.assembly_hourly_usd
        surcharge = allowance.assembly_keyterms_usd if self.keyterms else Decimal(0)
        if base is None or surcharge is None:
            return None
        return base + surcharge

    @property
    def rate_verified(self):
        return 0 <= (self.today - PRICING_DATE).days <= PRICE_MAX_AGE_DAYS

    @property
    def assembly_reserve(self):
        if self.channel_rate is None:
            return None
        return (self.channel_rate * CHANNELS * (self.options.minutes * 60 + SHUTDOWN_RESERVE_SECONDS)
                / 3600 * MARGIN)

    @property
    def groq_reserve_tokens(self):
        return ceiling(Decimal(self.options.max_answers * (MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS)) * MARGIN)

    @property
    def groq_reserve_requests(self):
        return ceiling(Decimal(self.options.max_answers + 1) * MARGIN)

    def blockers(self):
        opt, free = self.options, self.options.free
        reasons = []
        if opt.cap_ugx != 0:
            reasons.append('FREE USAGE ONLY requires maximum user expenditure of UGX 0.')
        if not 0 < self.max_minutes <= 1440 or opt.minutes > self.max_minutes:
            reasons.append('Choose a duration within the configured maximum.')
        if not opt.assembly_billing_checked or free.assembly_credits_usd is None or not free.assembly_source:
            reasons.append('Free allowance not confirmed for AssemblyAI.')
        if not opt.groq_billing_checked or free.groq_remaining_requests is None or free.groq_remaining_tokens is None or not free.groq_source:
            reasons.append('Free allowance not confirmed for Groq.')
        if free.assembly_plan != 'unupgraded_trial' or not free.assembly_no_paid_fallback:
            reasons.append('AssemblyAI zero-billing protection unconfirmed. Use an unupgraded trial with no payment method, invoice agreement or auto-pay.')
        if free.groq_plan != 'free' or not free.groq_no_paid_fallback:
            reasons.append('Groq cannot guarantee zero billable usage with this setup. Confirm Free plan with no paid billing, auto-reload or invoice agreement.')
        if not free.exclusive_balances:
            reasons.append('Reserve these organisation balances for this session; pause other API usage and billing changes.')
        now = datetime.now(timezone.utc)
        checked, until = free.checked_at, free.valid_until
        if (checked is None or checked.utcoffset() is None or not timedelta(0) <= now - checked <= timedelta(minutes=15)):
            reasons.append('Confirm current free balances and billing protections within the last 15 minutes.')
        if until is None or until.utcoffset() is None or until <= now + timedelta(minutes=opt.minutes, seconds=30):
            reasons.append('Confirm that free allowances remain valid through the entire session and shutdown reserve.')
        if self.channel_rate is None:
            reasons.append('AssemblyAI cost unknown. Enter the account rate for universal-streaming-english and any enabled keyterm surcharge.')
        if not self.rate_verified or self.model not in GROQ_PRICES:
            reasons.append('Groq gross usage pricing unknown or stale for the selected model.')
        if free.assembly_credits_usd is not None:
            if free.assembly_credits_usd == 0:
                reasons.append('AssemblyAI free allowance exhausted.')
            elif self.assembly_reserve is not None and free.assembly_credits_usd < self.assembly_reserve:
                reasons.append('Insufficient free credits for AssemblyAI for both streams, duration and safety margin.')
        if free.groq_remaining_tokens is not None and free.groq_remaining_requests is not None:
            if free.groq_remaining_tokens == 0 or free.groq_remaining_requests == 0:
                reasons.append('Groq free allowance exhausted.')
            elif (free.groq_remaining_tokens < self.groq_reserve_tokens
                  or free.groq_remaining_requests < self.groq_reserve_requests):
                reasons.append('Insufficient free credits or quota for the requested Groq answers and safety margin.')
        return reasons

    def require_authorisation(self):
        if not self.options.confirmed:
            raise ValueError('Explicit authorisation is required for each free live session.')
        if reasons := self.blockers():
            raise ValueError(' '.join(reasons))

    def gross_ugx_reference(self):
        opt = self.options
        if (self.channel_rate is None or opt.fx_ugx_per_usd is None or not opt.fx_source
                or opt.fx_date is None or not 0 <= (self.today - opt.fx_date).days <= FX_MAX_AGE_DAYS):
            return None
        return ceiling(self.channel_rate * CHANNELS * opt.minutes / 60 * opt.fx_ugx_per_usd)

    def public(self):
        hourly = self.channel_rate * CHANNELS if self.channel_rate is not None else None
        reasons = self.blockers()
        prices = GROQ_PRICES.get(self.model) if self.rate_verified else None
        groq_gross = None
        if prices:
            groq_gross = (MAX_INPUT_TOKENS * prices[0] + MAX_OUTPUT_TOKENS * prices[1]) * self.options.max_answers / 1000000
        def usd(value):
            return str(value.quantize(Decimal('0.000001'), rounding=ROUND_CEILING)) if value is not None else None
        return {
            'mode': 'FREE USAGE ONLY', 'pricing_source': PRICING_SOURCE,
            'pricing_date': PRICING_DATE.isoformat(), 'groq_pricing_source': GROQ_SOURCE,
            'currency': 'USD', 'channels': CHANNELS, 'minutes': self.options.minutes,
            'base_per_channel_hour_usd': usd(self.options.free.assembly_hourly_usd),
            'keyterms_per_channel_hour_usd': usd(self.options.free.assembly_keyterms_usd if self.keyterms else Decimal(0)),
            'hourly_usd': usd(hourly), 'total_usd': usd(hourly * self.options.minutes / 60) if hourly is not None else None,
            'assembly_reserved_usd': usd(self.assembly_reserve),
            'assembly_available_usd': usd(self.options.free.assembly_credits_usd),
            'groq_gross_usd': usd(groq_gross), 'groq_reserved_tokens': self.groq_reserve_tokens,
            'groq_reserved_requests': self.groq_reserve_requests, 'max_answers': self.options.max_answers,
            'groq_available_tokens': self.options.free.groq_remaining_tokens,
            'groq_available_requests': self.options.free.groq_remaining_requests,
            'gross_transcription_ugx_reference': self.gross_ugx_reference(),
            'total_ugx': 0 if not reasons else None, 'expected_payment_ugx': 0 if not reasons else None,
            'blockers': reasons, 'safety_margin_percent': 25,
            'input_reservation_tokens_per_answer': MAX_INPUT_TOKENS,
            'output_reservation_tokens_per_answer': MAX_OUTPUT_TOKENS,
            'account_verification': 'User-confirmed; account balances and billing state are not read by the app.',
        }


def quote(options, keyterms, max_minutes, model='openai/gpt-oss-20b'):
    return Quote(options, keyterms, max_minutes, date.today(), model)


class FreeSessionBudget:
    """Reserve before requesting. Failed or cancelled calls retain their reservation."""
    def __init__(self, pricing):
        self.remaining_answers = pricing.options.max_answers
        self.assembly_reserved_usd = pricing.assembly_reserve
        self.remaining_tokens = pricing.options.free.groq_remaining_tokens
        self.remaining_requests = pricing.options.free.groq_remaining_requests - 1  # Metadata verification.
        self.failed = False

    def reserve_answer(self):
        if self.failed or self.remaining_answers <= 0:
            raise ValueError('Groq free session allowance exhausted. STOP ALL and reconfirm free balances before another session.')
        tokens = MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS
        if self.remaining_tokens < tokens or self.remaining_requests < 1:
            self.failed = True
            raise ValueError('Groq free allowance exhausted.')
        self.remaining_answers -= 1
        self.remaining_tokens -= tokens
        self.remaining_requests -= 1

    def public(self):
        return {'remaining_answers': self.remaining_answers, 'unreserved_groq_tokens': self.remaining_tokens,
                'unreserved_groq_requests': self.remaining_requests, 'expected_payment_ugx': 0}
