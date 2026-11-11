"""Offline pricing and authorisation for one live session."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DEFAULT_MINUTES = 5
CHANNELS = 2
PRICING_SOURCE = 'https://www.assemblyai.com/pricing'
PRICING_DATE = date(2026, 11, 11)
BASE_USD = Decimal('0.15')
KEYTERMS_USD = Decimal('0.04')
PRICE_MAX_AGE_DAYS = 30
FX_MAX_AGE_DAYS = 7
COST_UNKNOWN = 'COST UNKNOWN — LIVE SESSION BLOCKED'


class LiveOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    minutes: int = Field(default=DEFAULT_MINUTES, strict=True, ge=1, le=1440)
    cap_ugx: int = Field(default=0, strict=True, ge=0, le=1_000_000_000)
    fx_ugx_per_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    fx_date: date | None = None
    fx_source: str = Field(default='', max_length=180)
    assembly_billing_checked: StrictBool = False
    groq_billing_checked: StrictBool = False
    confirmed: StrictBool = False


@dataclass(frozen=True)
class Quote:
    options: LiveOptions
    keyterms: bool
    max_minutes: float
    today: date

    @property
    def channel_rate(self):
        return BASE_USD + (KEYTERMS_USD if self.keyterms else Decimal(0))

    @property
    def rate_verified(self):
        return 0 <= (self.today - PRICING_DATE).days <= PRICE_MAX_AGE_DAYS

    @property
    def fx_verified(self):
        opt = self.options
        return bool(opt.fx_ugx_per_usd and opt.fx_source and opt.fx_date
                    and 0 <= (self.today - opt.fx_date).days <= FX_MAX_AGE_DAYS)

    @property
    def total_ugx(self):
        if not (self.rate_verified and self.fx_verified):
            return None
        amount = self.channel_rate * CHANNELS * self.options.minutes * self.options.fx_ugx_per_usd
        return int((amount / 60).to_integral_value(rounding=ROUND_CEILING))

    def blockers(self):
        opt = self.options
        reasons = []
        if not 0 < self.max_minutes <= 1440 or opt.minutes > self.max_minutes:
            reasons.append('Choose a duration within the configured maximum.')
        if self.total_ugx is None:
            reasons.append(COST_UNKNOWN)
            if not self.rate_verified:
                reasons.append('The published pricing snapshot needs to be reverified.')
            if not self.fx_verified:
                reasons.append('Enter a checked USD-to-UGX rate, source, and date within the last seven days.')
        elif self.total_ugx > opt.cap_ugx:
            reasons.append(f'The estimate of UGX {self.total_ugx:,} exceeds your UGX {opt.cap_ugx:,} limit.')
        if opt.cap_ugx == 0 and self.total_ugx is None:
            reasons.append('The UGX 0 limit blocks any non-zero estimated spend.')
        if not opt.assembly_billing_checked or not opt.groq_billing_checked:
            reasons.append('Billing, free allowances and remaining credits must be checked for both providers.')
        return reasons

    def require_authorisation(self):
        if not self.options.confirmed:
            raise ValueError('Explicit authorisation is required for each live session.')
        reasons = self.blockers()
        if reasons:
            raise ValueError(' '.join(reasons))

    def public(self):
        opt = self.options
        hourly = self.channel_rate * CHANNELS
        return {
            'pricing_source': PRICING_SOURCE, 'pricing_date': PRICING_DATE.isoformat(),
            'currency': 'USD', 'channels': CHANNELS,
            'base_per_channel_hour_usd': str(BASE_USD),
            'keyterms_per_channel_hour_usd': str(KEYTERMS_USD if self.keyterms else Decimal(0)),
            'hourly_usd': str(hourly) if self.rate_verified else None,
            'total_usd': str((hourly * opt.minutes / 60).quantize(
                Decimal('0.0001'), rounding=ROUND_CEILING)) if self.rate_verified else None,
            'total_ugx': self.total_ugx, 'minutes': opt.minutes,
            'blockers': self.blockers(),
        }


def quote(options, keyterms, max_minutes):
    return Quote(options, keyterms, max_minutes, date.today())
