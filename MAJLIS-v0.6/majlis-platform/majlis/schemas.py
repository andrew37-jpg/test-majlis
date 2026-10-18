from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

Id = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NewOrder(Input):
    number: str = Field(min_length=1, max_length=64)
    customer_id: Id
    customer_name: str = Field(min_length=1, max_length=100)
    item: str = Field(min_length=1, max_length=300)
    amount: Annotated[int, Field(strict=True, gt=0, le=1000000000000)]
    shipment: str = Field(default="", max_length=100)
    eta: date | None = None

    @field_validator("eta", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value


class Revision(Input):
    order_id: Id
    version: Annotated[int, Field(strict=True, gt=0)]


class Status(Revision):
    status: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=500)


class Details(Revision):
    customer_id: Id
    transfer_confirmed: bool = Field(default=False, strict=True)
    customer_name: str | None = Field(default=None, min_length=1, max_length=100)
    shipment: str = Field(default="", max_length=100)
    eta: date | None = None
    eta_confirmed: bool = Field(default=False, strict=True)

    @field_validator("eta", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value


class Payment(Revision):
    url: str = Field(min_length=0, max_length=2000)


class Integration(Input):
    event_id: str = Field(min_length=1, max_length=128)
    shipment: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=32)

    sequence: Annotated[int | None, Field(strict=True, gt=0, le=9223372036854775807)] = None


class Commercial(Revision):
    item: str = Field(min_length=1, max_length=300)
    amount: Annotated[int, Field(strict=True, gt=0, le=1000000000000)]
