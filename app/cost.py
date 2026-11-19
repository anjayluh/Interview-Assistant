"""Local live-session policy. Provider free allowances are enforced by providers."""
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictBool

DEFAULT_MINUTES = 5
MAX_INPUT_TOKENS = 32768
MAX_OUTPUT_TOKENS = 2000


class LiveOptions(BaseModel):
    # Older tabs may submit the removed balance/FX fields. They confer no authority.
    model_config = ConfigDict(extra='ignore')
    minutes: int = Field(default=DEFAULT_MINUTES, strict=True, ge=1, le=1440)
    cap_ugx: int = Field(default=0, strict=True, ge=0, le=1000000000)
    confirmed: StrictBool = False


@dataclass(frozen=True)
class Quote:
    options: LiveOptions
    max_minutes: float

    def blockers(self):
        reasons = []
        if self.options.cap_ugx != 0:
            reasons.append('Maximum user expenditure must remain UGX 0.')
        if not 0 < self.max_minutes <= 1440 or self.options.minutes > self.max_minutes:
            reasons.append('Choose a duration within the configured maximum.')
        return reasons

    def require_authorisation(self):
        if not self.options.confirmed:
            raise ValueError('Explicit authorisation is required for each free live session.')
        if reasons := self.blockers():
            raise ValueError(' '.join(reasons))

    def public(self):
        return {
            'mode': 'FREE USAGE ONLY',
            'minutes': self.options.minutes,
            'maximum_payment_ugx': 0,
            'allowance_status': 'Use the confirmed AssemblyAI trial and Groq Free accounts. Providers determine current allowance availability.',
            'blockers': self.blockers(),
        }


def quote(options, max_minutes):
    return Quote(options, max_minutes)
