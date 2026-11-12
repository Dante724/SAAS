"""Who may see which task.

Managers (a role with can_assign_staff, and the shop owner) see every task in the shop. Everyone
else sees the tasks assigned to them (primary owner or one of the extra assignees), the tasks they
made, and unassigned tasks (the shop's shared pool). A task outside that is answered with 404, as
if it did not exist.

Assignees live in two columns: `assigned_to_id` (the primary owner) and `assignee_ids` (a JSON list
that normally includes the primary). "Is X in assignee_ids" compiles to a jsonb containment on
Postgres, served by the GIN index ix_tasks_assignee_ids_gin, and to a quoted-id match on the JSON
text elsewhere (SQLite, where shops are small). With ix_tasks_org_assignee and ix_tasks_org_creator
every arm of "assigned to me or made by me" has an index Postgres can BitmapOr.
"""
from sqlalchemy import Boolean, String, and_, cast, func, literal, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement

from backend.models import Task

MANAGE_PERM = "can_assign_staff"


class list_has(ColumnElement):
    """`json_list_column` contains `value`: an employee id, or a column of ids (Employee.id, so one
    grouped query can count every person's tasks)."""
    type = Boolean()
    inherit_cache = False

    def __init__(self, column, value: str):
        self.column = column
        self.value = value


@compiles(list_has)
def _list_has_text(el, compiler, **kw):
    if isinstance(el.value, str):
        return compiler.process(cast(el.column, String).contains(f'"{el.value}"', autoescape=True), **kw)
    # A column: instr() is an exact substring test (LIKE would read "_" in an id as a wildcard).
    quoted = literal('"', String) + el.value + literal('"', String)
    return compiler.process(func.instr(cast(el.column, String), quoted) > 0, **kw)


@compiles(list_has, "postgresql")
def _list_has_jsonb(el, compiler, **kw):
    return compiler.process(cast(el.column, JSONB).op("@>")(func.jsonb_build_array(el.value)), **kw)


def assigned_sql(emp_id: str):
    """The employee is the primary owner or one of the extra assignees."""
    return or_(Task.assigned_to_id == emp_id, list_has(Task.assignee_ids, emp_id))


def involves_sql(emp_id: str):
    """Assigned to the employee, or made by them."""
    return or_(assigned_sql(emp_id), Task.created_by_id == emp_id)


def unassigned_sql():
    """Nobody is assigned: no primary owner and an empty (or missing) assignee list."""
    return and_(Task.assigned_to_id.is_(None),
                or_(Task.assignee_ids.is_(None), cast(Task.assignee_ids, String).in_(("[]", "null"))))


def visible_sql(emp_id: str):
    return or_(involves_sql(emp_id), unassigned_sql())


def own_day_sql(emp_id: str):
    """Whose day a task belongs to (My day): assigned to them, or an unassigned to-do they made.
    Tasks they made for other people belong to those people's day."""
    return or_(assigned_sql(emp_id), and_(Task.created_by_id == emp_id, unassigned_sql()))


async def sees_all(db, employee) -> bool:
    from backend.services.auth import has_perm
    return await has_perm(db, employee, MANAGE_PERM)


async def visibility_conds(db, employee) -> list:
    """Extra WHERE conditions limiting a task query to what `employee` may see ([] for managers).
    Always combine with Task.org_id == employee.org_id."""
    return [] if await sees_all(db, employee) else [visible_sql(employee.id)]
