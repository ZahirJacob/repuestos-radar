"""Ajustes: repair price list and tracked-parts management, phone-easy.

Same write helpers as the team CLIs (services.py / tracked.py) — the admin
page is another caller, not another implementation.
"""

import streamlit as st
from sqlalchemy.orm import Session

from repuestos_radar import services, tracked
from repuestos_radar.dashboard import data, quicksearch, text_es
from repuestos_radar.models import TrackedItem
from repuestos_radar.report import format_ars
from repuestos_radar.sources import load_sources

# A success message shown right before st.rerun() never renders — the rerun
# discards it. Instead every mutating path stashes a flash here and render()
# shows it at the top of the NEXT run.
_FLASH_KEY = "admin-flash"


def _flash(state, text: str, *, kind: str = "success") -> None:
    state[_FLASH_KEY] = (kind, text)


def _pop_flash(state) -> tuple[str, str] | None:
    return state.pop(_FLASH_KEY, None)


def _price_error(reason: str | None) -> str | None:
    if reason == "not a number":
        return text_es.PRICE_NOT_A_NUMBER
    if reason == "not positive":
        return text_es.PRICE_NOT_POSITIVE
    return None


def _add_service(
    session: Session, label: str, item_id: int, raw_price: str
) -> tuple[str | None, str | None]:
    """Validate and upsert; returns (Spanish error, Spanish success message).

    Exactly one side is set. An upsert that replaced an existing label gets
    its own message — a silent overwrite reported as "Guardado." would hide
    that the old price and part link are gone.
    """
    label = label.strip()
    if not label:
        return text_es.LABEL_EMPTY, None
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason), None
    _, status = services.add_service(session, label, item_id, price)
    session.commit()
    if status == services.UPDATED:
        return None, text_es.SERVICE_UPDATED_EXISTING
    return None, text_es.SERVICE_SAVED


def _set_service_price(session: Session, service_id: int, raw_price: str) -> str | None:
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason)
    _, status = services.set_price(session, service_id, price)
    if status == services.NOT_FOUND:
        # The row vanished under us (deleted from another session/CLI) —
        # "Guardado." would be a lie.
        return text_es.SERVICE_NOT_FOUND
    session.commit()
    return None


def _skipped_note(report: quicksearch.QuickSearchReport) -> str | None:
    names = [s.name for s in report.sources if not s.searched]
    if not names:
        return None
    return text_es.QUICK_SEARCH_SKIPPED_NOTE.format(names=", ".join(names))


def _blocked_note(report: quicksearch.QuickSearchReport) -> str | None:
    if not report.blocked:
        return None
    names = [s.name for s in report.blocked]
    return text_es.QUICK_SEARCH_BLOCKED_NOTE.format(names=", ".join(names))


def _render_report(report: quicksearch.QuickSearchReport) -> None:
    if report.capped:
        st.info(text_es.QUICK_SEARCH_CAP.format(cap=quicksearch.DAILY_CAP))
        return
    for source_report in report.sources:
        if source_report.searched and source_report.failure is not None:
            st.warning(text_es.QUICK_SEARCH_SOURCE_FAILED.format(name=source_report.name))
    for note in (_skipped_note(report), _blocked_note(report)):
        if note:
            st.caption(note)
    st.success(text_es.QUICK_SEARCH_DONE)


def _render_quick_search(session: Session) -> None:
    st.subheader(text_es.QUICK_SEARCH_HEADER)
    used = quicksearch.runs_today(session)
    st.caption(text_es.QUICK_SEARCH_USED.format(used=used, cap=quicksearch.DAILY_CAP))
    items = {item.id: item.query for item in tracked.list_items(session) if item.active}
    if not items:
        st.caption(text_es.QUICK_SEARCH_NO_ITEMS)
        return
    preselect = st.session_state.get("quick-search-item")
    ids = list(items)
    index = ids.index(preselect) if preselect in items else 0
    item_id = st.selectbox(text_es.QUICK_SEARCH_ITEM_FIELD, ids, index=index, format_func=items.get)
    capped = used >= quicksearch.DAILY_CAP
    if capped:
        st.info(text_es.QUICK_SEARCH_CAP.format(cap=quicksearch.DAILY_CAP))
    if st.button(text_es.QUICK_SEARCH_BUTTON, disabled=capped, use_container_width=True):
        item = session.get(TrackedItem, item_id)
        with st.status(text_es.QUICK_SEARCH_RUNNING, expanded=True) as status:
            try:
                report = quicksearch.quick_search(
                    session,
                    item,
                    load_sources(),
                    progress=lambda name: status.write(
                        text_es.QUICK_SEARCH_PROGRESS.format(name=name)
                    ),
                )
            except quicksearch.QuickSearchBusy:
                status.update(label=text_es.QUICK_SEARCH_BUSY, state="error")
                return
        # Rerun so the "Búsquedas de hoy" counter and the button's disabled
        # state reflect the run just recorded; the report is stashed and
        # rendered right here on the next run so it stays on screen.
        st.session_state["quick-search-report"] = report
        st.rerun()
    report = st.session_state.pop("quick-search-report", None)
    if report is not None:
        _render_report(report)


def _render_services(session: Session) -> None:
    st.subheader(text_es.SERVICES_HEADER)
    items = {item.id: item.query for item in tracked.list_items(session)}
    for service in services.list_services(session):
        with st.container(border=True):
            st.markdown(f"**{service.label}** — {format_ars(service.price_ars)}")
            with st.expander(text_es.SERVICE_EDIT):
                raw = st.text_input(
                    text_es.SERVICE_PRICE_FIELD,
                    value=str(service.price_ars),
                    key=f"price-{service.id}",
                )
                if st.button(text_es.SERVICE_SAVE, key=f"save-{service.id}"):
                    error = _set_service_price(session, service.id, raw)
                    if error:
                        st.error(error)
                    else:
                        _flash(st.session_state, text_es.SERVICE_SAVED)
                        st.rerun()
                confirm_key = f"confirm-service-{service.id}"
                if st.session_state.get(confirm_key):
                    st.warning(text_es.SERVICE_CONFIRM)
                    yes, no = st.columns(2)
                    if yes.button(text_es.SERVICE_CONFIRM_YES, key=f"yes-{service.id}"):
                        services.remove_service(session, service.id)
                        session.commit()
                        st.session_state.pop(confirm_key)
                        _flash(st.session_state, text_es.SERVICE_REMOVED)
                        st.rerun()
                    if no.button(text_es.SERVICE_CONFIRM_NO, key=f"no-{service.id}"):
                        st.session_state.pop(confirm_key)
                        st.rerun()
                elif st.button(text_es.SERVICE_REMOVE, key=f"rm-{service.id}"):
                    st.session_state[confirm_key] = True
                    st.rerun()

    # No clear_on_submit: it would wipe all three fields on a validation
    # error too (a price typo costing the label and part again). Instead the
    # success path clears the text fields itself, via a flag consumed above
    # BEFORE the widgets are instantiated on the next run.
    if st.session_state.pop("add-service-clear", False):
        st.session_state.pop("add-service-label", None)
        st.session_state.pop("add-service-price", None)
    with st.form("add-service"):
        st.markdown(f"**{text_es.SERVICE_ADD_HEADER}**")
        label = st.text_input(text_es.SERVICE_LABEL_FIELD, key="add-service-label")
        item_id = (
            st.selectbox(text_es.SERVICE_ITEM_FIELD, list(items), format_func=items.get)
            if items
            else None
        )
        raw_price = st.text_input(text_es.SERVICE_PRICE_FIELD, key="add-service-price")
        if st.form_submit_button(text_es.SERVICE_ADD_BUTTON) and item_id is not None:
            error, saved = _add_service(session, label, item_id, raw_price)
            if error:
                st.error(error)
            else:
                _flash(st.session_state, saved)
                st.session_state["add-service-clear"] = True
                st.rerun()


def _render_tracked(session: Session) -> None:
    st.subheader(text_es.TRACKED_HEADER)
    for item in tracked.list_items(session):
        if not item.active:
            continue
        with st.container(border=True):
            st.markdown(f"**{item.query}**")
            confirm_key = f"confirm-tracked-{item.id}"
            if st.session_state.get(confirm_key):
                st.warning(text_es.TRACKED_STOP_WARNING)
                yes, no = st.columns(2)
                if yes.button(text_es.TRACKED_CONFIRM_YES, key=f"tyes-{item.id}"):
                    tracked.set_active(session, item.id, False)
                    session.commit()
                    st.session_state.pop(confirm_key)
                    _flash(st.session_state, text_es.TRACKED_STOPPED)
                    st.rerun()
                if no.button(text_es.SERVICE_CONFIRM_NO, key=f"tno-{item.id}"):
                    st.session_state.pop(confirm_key)
                    st.rerun()
            elif st.button(text_es.TRACKED_STOP, key=f"tstop-{item.id}"):
                st.session_state[confirm_key] = True
                st.rerun()

    with st.form("add-tracked", clear_on_submit=True):
        st.markdown(f"**{text_es.TRACKED_ADD_HEADER}**")
        query = st.text_input(text_es.TRACKED_QUERY_FIELD, help=text_es.TRACKED_QUERY_HINT)
        if st.form_submit_button(text_es.TRACKED_ADD_BUTTON):
            query = query.strip()
            if query:
                item, status = tracked.add_item(session, query)
                session.commit()
                if status == tracked.ALREADY_ACTIVE:
                    _flash(st.session_state, text_es.TRACKED_ALREADY, kind="info")
                else:
                    _flash(st.session_state, text_es.TRACKED_ADDED)
                    st.session_state["quick-search-item"] = item.id
                st.rerun()


def render() -> None:
    st.title(text_es.NAV_SETTINGS)
    flash = _pop_flash(st.session_state)
    if flash is not None:
        kind, text = flash
        (st.info if kind == "info" else st.success)(text)
    with data.open_session() as session:
        _render_quick_search(session)
        st.divider()
        _render_services(session)
        st.divider()
        _render_tracked(session)
