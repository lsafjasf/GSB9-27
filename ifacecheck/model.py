"""接口定义数据模型：参数 / 返回结构 / 函数 / 整个接口。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

NO_DEFAULT = object()  # sentinel：表示该参数没有默认值


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "any"
    required: bool = True
    default: Any = NO_DEFAULT


@dataclass(frozen=True)
class ReturnField:
    name: str
    type: str = "any"
    required: bool = True


@dataclass
class Function:
    name: str
    params: list[Param] = field(default_factory=list)
    return_type: str = "any"
    return_fields: list[ReturnField] = field(default_factory=list)

    def param(self, name: str) -> Param | None:
        for p in self.params:
            if p.name == name:
                return p
        return None


@dataclass
class Interface:
    name: str
    version: str
    functions: dict[str, Function] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Interface":
        funcs: dict[str, Function] = {}
        for f in data.get("functions", []):
            params: list[Param] = []
            for p in f.get("params", []):
                default = p.get("default", NO_DEFAULT)
                params.append(Param(
                    name=p["name"],
                    type=p.get("type", "any"),
                    required=p.get("required", default is NO_DEFAULT),
                    default=default,
                ))
            ret = f.get("returns") or {}
            fields_ = [
                ReturnField(
                    name=rf["name"],
                    type=rf.get("type", "any"),
                    required=rf.get("required", True),
                )
                for rf in ret.get("fields", [])
            ]
            funcs[f["name"]] = Function(
                name=f["name"],
                params=params,
                return_type=ret.get("type", "any"),
                return_fields=fields_,
            )
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            functions=funcs,
        )

    @classmethod
    def from_json_file(cls, path: str) -> "Interface":
        with open(path, "r", encoding="utf-8") as fp:
            return cls.from_dict(json.load(fp))
