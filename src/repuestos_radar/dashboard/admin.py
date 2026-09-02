"""Ajustes: repair price list and tracked-parts management, phone-easy.

Same write helpers as the team CLIs (services.py / tracked.py) — the admin
page is another caller, not another implementation.
"""

import streamlit as st
from sqlalchemy.orm import Session

from repuestos_radar import services, tracked
from repuestos_radar.dashboard import data, text_es
from repuestos_radar.report import format_ars


def _price_error(reason: str | None) -> str | None:
    if reason == "not a number":
        return text_es.PRICE_NOT_A_NUMBER
    if reason == "not positive":
        return text_es.PRICE_NOT_POSITIVE
    return None


def _add_service(session: Session, label: str, item_id: int, raw_price: str) -> str | None:
    """Validate and upsert; returns a Spanish error, or None on success."""
    label = label.strip()
    if not label:
        return text_es.LABEL_EMPTY
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason)
    services.add_service(session, label, item_id, price)
    session.commit()
    return None


def _set_service_price(session: Session, service_id: int, raw_price: str) -> str | None:
    price, reason = services.parse_price(raw_price)
    if price is None:
        return _price_error(reason)
    services.set_price(session, service_id, price)
    session.commit()
    return None


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
                        st.success(text_es.SERVICE_SAVED)
                        st.rerun()
                confirm_key = f"confirm-service-{service.id}"
                if st.session_state.get(confirm_key):
                    st.warning(text_es.SERVICE_CONFIRM)
                    yes, no = st.columns(2)
                    if yes.button(text_es.SERVICE_CONFIRM_YES, key=f"yes-{service.id}"):
                        services.remove_service(session, service.id)
                        session.commit()
                        st.session_state.pop(confirm_key)
                        st.success(text_es.SERVICE_REMOVED)
                        st.rerun()
                    if no.button(text_es.SERVICE_CONFIRM_NO, key=f"no-{service.id}"):
                        st.session_state.pop(confirm_key)
                        st.rerun()
                elif st.button(text_es.SERVICE_REMOVE, key=f"rm-{service.id}"):
                    st.session_state[confirm_key] = True
                    st.rerun()

    with st.form("add-service", clear_on_submit=True):
        st.markdown(f"**{text_es.SERVICE_ADD_HEADER}**")
        label = st.text_input(text_es.SERVICE_LABEL_FIELD)
        item_id = (
            st.selectbox(text_es.SERVICE_ITEM_FIELD, list(items), format_func=items.get)
            if items
            else None
        )
        raw_price = st.text_input(text_es.SERVICE_PRICE_FIELD)
        if st.form_submit_button(text_es.SERVICE_ADD_BUTTON) and item_id is not None:
            error = _add_service(session, label, item_id, raw_price)
            if error:
                st.error(error)
            else:
                st.success(text_es.SERVICE_SAVED)
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
                if yes.button(text_es.SERVICE_CONFIRM_YES, key=f"tyes-{item.id}"):
                    tracked.set_active(session, item.id, False)
                    session.commit()
                    st.session_state.pop(confirm_key)
                    st.success(text_es.TRACKED_STOPPED)
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
                    st.info(text_es.TRACKED_ALREADY)
                else:
                    st.success(text_es.TRACKED_ADDED)
                    st.session_state["quick-search-item"] = item.id
                st.rerun()


def render() -> None:
    st.title(text_es.NAV_SETTINGS)
    with data.open_session() as session:
        _render_services(session)
        st.divider()
        _render_tracked(session)
