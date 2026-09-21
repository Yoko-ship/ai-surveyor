"""
Кто какие запросы видит. Одно место для правила «вижу только своё».

Основание — docs/Регистрация и роли.md, раздел 6.1:
  п. 1  любая выборка запросов для роли «сотрудник» фильтруется по автору, причём в слое доступа
        к данным (в SQL), а не отсечением списка после выборки;
  п. 2  прямой доступ по идентификатору к чужому запросу отдаёт 404, а не 403: 403 подсказывает
        постороннему, что такой запрос вообще существует.
Правовая рамка — тайна страхования: ЗРУ-730 ст. 62, ГК ст. 933, ЗРУ-547 ст. 12/27/31.

Кто видит всё: андеррайтер, актуарий, админ. Остальные (сотрудник, агент) — только запросы,
где они автор, где они привязаны как агент по ID в ЕАИС или где они назначены согласующими.
"""
from typing import List, Optional

from fastapi import HTTPException

from . import db

# роли, которым открыты все запросы компании
FULL_VIEW_ROLES = ("андеррайтер", "актуарий", "админ")

NOT_FOUND = "Запрос не найден"


def sees_all(user: Optional[dict]) -> bool:
    return bool(user) and user.get("role") in FULL_VIEW_ROLES


def own_requests_where(alias: str = "r") -> str:
    """Кусок WHERE «только свои запросы». Три связи: автор, агент по ID в ЕАИС, назначенный согласующий."""
    return (f" AND ({alias}.created_by_user_id = ?"
            f" OR ({alias}.agent_id IS NOT NULL AND {alias}.agent_id IN"
            f"     (SELECT id FROM agents WHERE eais_id IS NOT NULL AND eais_id <> '' AND eais_id = ?))"
            f" OR {alias}.id IN (SELECT request_id FROM request_reviewers WHERE user_id = ?))")


def own_requests_args(user: dict) -> List:
    return [user["id"], user.get("agent_eais_id") or "", user["id"]]


def participants(con, rid: int) -> set:
    """Автор запроса (напрямую и через ID агента в ЕАИС) плюс назначенные согласующие."""
    from . import approvals                      # ленивый импорт: approvals тянет auth, а тот — db
    ids = set(approvals.initiator_user_ids(con, rid))
    ids |= {r["user_id"] for r in db.rows(con, "SELECT user_id FROM request_reviewers WHERE request_id=?", rid)}
    return {i for i in ids if i}


def can_open(con, user: dict, rid: int) -> bool:
    if not db.rows(con, "SELECT 1 FROM requests WHERE id=?", rid):
        return False
    if sees_all(user):
        return True
    return user["id"] in participants(con, rid)


def ensure_can_open(con, user: dict, rid: int):
    """404 и для несуществующего запроса, и для чужого — снаружи эти случаи неразличимы."""
    if not can_open(con, user, rid):
        raise HTTPException(404, NOT_FOUND)
