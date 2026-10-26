"""Address identity and plan parsing, independent of browser/network state."""
import re
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STATES = set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'.split())

class Address(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    street: str = Field(min_length=3, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    state: str
    zip_code: str = Field(pattern=r'^\d{5}(?:-\d{4})?$')
    unit: str | None = Field(default=None, max_length=40)

    @field_validator('street', 'city', 'unit')
    @classmethod
    def plain_text(cls, value):
        if value and (re.search(r'[<>\x00-\x1f]', value) or '\n' in value):
            raise ValueError('Use a plain-text address')
        return value or None

    @field_validator('street')
    @classmethod
    def street_number(cls, value):
        if not re.match(r'^\d+[\w-]*\s+\S', value):
            raise ValueError('Include the street number and street name')
        if re.search(r'\b(?:apt|apartment|unit|suite)\b|#', value, re.I):
            raise ValueError('Enter apartment/unit in its separate field')
        return value

    @field_validator('state')
    @classmethod
    def state_code(cls, value):
        value = value.upper()
        if value not in STATES:
            raise ValueError('Use a two-letter US state or territory code')
        return value

    def label(self):
        return f'{self.street}{", Unit " + self.unit if self.unit else ""}, {self.city}, {self.state} {self.zip_code}'

WORDS = {'STREET':'ST', 'AVENUE':'AVE', 'ROAD':'RD', 'DRIVE':'DR', 'BOULEVARD':'BLVD', 'LANE':'LN', 'COURT':'CT', 'PLACE':'PL', 'PARKWAY':'PKWY', 'HIGHWAY':'HWY', 'TERRACE':'TER', 'CIRCLE':'CIR', 'EAST':'E', 'WEST':'W', 'NORTH':'N', 'SOUTH':'S', 'APARTMENT':'UNIT', 'APT':'UNIT', 'SUITE':'UNIT'}

def normalize(value):
    value = re.sub(r'\bTHE\s+BRONX\b', 'BRONX', value.upper())
    return ' '.join(WORDS.get(w, w) for w in re.findall(r'[A-Z0-9]+', value))

def parse_address(label):
    label = re.sub(r',?\s*(?:USA|UNITED STATES)$', '', label.strip(), flags=re.I)
    match = re.fullmatch(r'(.+?),\s*([^,]+),\s*([A-Za-z]{2})[ ,]+(\d{5}(?:-\d{4})?)', label)
    if not match:
        return None
    street, city, state, zip_code = match.groups()
    unit_match = re.search(r'(?:,?\s+(?:APT\.?|APARTMENT|UNIT|SUITE|#)\s*)([\w -]+)$', street, re.I)
    unit = unit_match.group(1).strip() if unit_match else None
    if unit_match:
        street = street[:unit_match.start()].strip()
    try:
        return Address(street=street, city=city, state=state, zip_code=zip_code, unit=unit)
    except ValueError:
        return None

def same_address(a, b, *, allow_missing_unit=False):
    return (a.street.split()[0].upper() == b.street.split()[0].upper()
            and normalize(a.street) == normalize(b.street) and normalize(a.city) == normalize(b.city)
            and a.state == b.state and a.zip_code == b.zip_code
            and (normalize(a.unit or '') == normalize(b.unit or '') or (allow_missing_unit and not b.unit)))

class FullAddressInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    full_address: str = Field(min_length=8, max_length=300)
    unit: str | None = Field(default=None, max_length=40)

    @model_validator(mode='after')
    def validate_address(self):
        self.to_address()
        return self

    def to_address(self):
        address = parse_address(self.full_address)
        if not address:
            raise ValueError('Include street number and name, city, two-letter US state and ZIP, separated by commas. Address components are never guessed.')
        if address.unit:
            raise ValueError('Put the apartment/unit in the separate Apt field.')
        return Address(**{**address.model_dump(), 'unit': self.unit})

def verified_address(text, requested, selected=None):
    # A complete address line, not unrelated street/ZIP fragments elsewhere in the page.
    for line in text.splitlines():
        candidate = parse_address(line.strip())
        if candidate and same_address(requested, candidate):
            if selected and not same_address(selected, candidate):
                continue
            return candidate
    raise LookupFailure('address_mismatch', 'The plans page did not confirm the full requested service address and unit. No plans were saved.')

class LookupFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message

PLAN_PATTERN = re.compile(r'^\d+(?:\.\d+)?\s*(?:Mbps|Gig|Gbps)\s+Internet$', re.I)

def parse_card(name, card):
    if not PLAN_PATTERN.fullmatch(name):
        raise LookupFailure('extraction_failed', 'Unrecognized internet plan heading.')
    prices = re.findall(r'\$\s*([\d,]+(?:\.\d{2})?)\s*/\s*mo\.?', card, re.I)
    if len(prices) > 1:
        raise LookupFailure('extraction_failed', 'A plan card contains conflicting monthly prices.')
    speed = re.search(r'(\d+(?:\.\d+)?)\s*(Mbps|Gig|Gbps)', name, re.I)
    protection = re.search(r'\b\d+[-\s]+(?:year|month)s?\s+price\s+(?:protection|lock)', card, re.I)
    promo = protection or re.search(r'\b(?:for\s+)?(?:the\s+first\s+)?\d+\s+(?:months?|years?)\b', card, re.I)
    conditions = [line.strip() for line in card.splitlines() if re.search(r'auto\s*pay|bank|checking|paperless|tax|fee|new\s+(?:residential\s+)?customer|require|after|with |w/|subject|restriction', line, re.I)]
    return dict(provider='Optimum', plan_name=name, speed_mbps=float(Decimal(speed[1]) * (1 if speed[2].lower() == 'mbps' else 1000)),
                monthly_price_cents=int(Decimal(prices[0].replace(',', '')) * 100) if prices else None,
                currency='USD' if prices else None, promotional_period=promo[0] if promo else None,
                conditions='\n'.join(conditions) or None, supporting_card_text=card)

def page_failure(text):
    patterns = [
        ('access_denied', r'access denied|verify you are human|unusual traffic|request blocked|captcha', 'Optimum denied or challenged access. Availability could not be determined.'),
        ('quota_exceeded', r'quota exceeded|too many requests|rate limit', 'The lookup service quota or rate limit was reached.'),
        ('service_unavailable', r'(?:we (?:do not|don.t)|optimum (?:does not|doesn.t)) (?:currently )?(?:offer|provide) (?:service|services)|(?:service|services) (?:is |are )?not available (?:at|in)|outside (?:of )?our service area', 'Optimum reports service is unavailable at this address.'),
        ('address_not_found', r'(?:could not|couldn.t|unable to) (?:find|locate|verify) (?:your|the|this) address|no (?:matching )?addresses found', 'Optimum could not find this address.'),
    ]
    for code, pattern, message in patterns:
        if re.search(pattern, text, re.I):
            raise LookupFailure(code, message)
