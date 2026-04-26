"""
Activity Logging Utility Module

Provides functions for logging clinic activities to the activity_log table.
Logs are created with automatic timestamps and can include rich metadata.

All logging functions accept a database connection and perform inserts
within the same transaction as the triggering operation.
"""

import json
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


async def log_activity(
    conn,
    clinic_id: str,
    event_type: str,
    title: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Generic activity logging function.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        event_type: Type of event (e.g., 'appointment_created')
        title: Human-readable event title
        entity_type: Type of entity affected (optional)
        entity_id: ID of affected entity (optional)
        user_id: ID of user who triggered event (optional)
        meta: Custom JSON metadata (optional)

    Returns:
        Activity ID (UUID) or None if logging failed

    Raises:
        ValueError: If event_type or title are empty
    """
    if not event_type or not event_type.strip():
        raise ValueError("event_type cannot be empty")
    if not title or not title.strip():
        raise ValueError("title cannot be empty")

    try:
        # Serialize metadata to JSON if provided
        meta_json = json.dumps(meta) if meta else None

        # Insert activity log
        activity_id = await conn.fetchval(
            """
            INSERT INTO activity_log (clinic_id, event_type, title, entity_type, entity_id, user_id, meta)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            clinic_id,
            event_type.strip(),
            title.strip(),
            entity_type,
            entity_id,
            user_id,
            meta_json,
        )
        return str(activity_id)
    except Exception as e:
        logger.error(f"Failed to log activity: {e}", exc_info=True)
        return None


async def log_appointment_created(
    conn,
    clinic_id: str,
    appointment_id: str,
    patient_name: str,
    doctor_name: str,
    appointment_time: str,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
    appointment_type: str = "consultation",
    payment_status: str = "unpaid",
) -> Optional[str]:
    """
    Log appointment creation event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        appointment_id: Appointment UUID
        patient_name: Name of patient
        doctor_name: Name of doctor
        appointment_time: Appointment datetime (ISO format)
        user_id: User who created appointment (optional)
        reason: Appointment reason (optional)
        appointment_type: Type of appointment (default: 'consultation')
        payment_status: Payment status (default: 'unpaid')

    Returns:
        Activity ID or None
    """
    meta = {
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "appointment_time": appointment_time,
        "reason": reason,
        "appointment_type": appointment_type,
        "payment_status": payment_status,
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="appointment_created",
        title=f"Appointment created for {patient_name}",
        entity_type="appointment",
        entity_id=appointment_id,
        user_id=user_id,
        meta=meta,
    )


async def log_appointment_updated(
    conn,
    clinic_id: str,
    appointment_id: str,
    patient_name: str,
    doctor_name: str,
    user_id: Optional[str] = None,
    changes: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Log appointment modification event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        appointment_id: Appointment UUID
        patient_name: Name of patient
        doctor_name: Name of doctor
        user_id: User who updated appointment
        changes: Dictionary of changes made

    Returns:
        Activity ID or None
    """
    meta = {
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "changes": changes or {},
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="appointment_updated",
        title=f"Appointment updated for {patient_name}",
        entity_type="appointment",
        entity_id=appointment_id,
        user_id=user_id,
        meta=meta,
    )


async def log_appointment_cancelled(
    conn,
    clinic_id: str,
    appointment_id: str,
    patient_name: str,
    doctor_name: str,
    user_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[str]:
    """
    Log appointment cancellation event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        appointment_id: Appointment UUID
        patient_name: Name of patient
        doctor_name: Name of doctor
        user_id: User who cancelled (optional)
        reason: Cancellation reason (optional)

    Returns:
        Activity ID or None
    """
    meta = {
        "patient_name": patient_name,
        "doctor_name": doctor_name,
        "reason": reason,
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="appointment_cancelled",
        title=f"Appointment cancelled for {patient_name}",
        entity_type="appointment",
        entity_id=appointment_id,
        user_id=user_id,
        meta=meta,
    )


async def log_call_logged(
    conn,
    clinic_id: str,
    call_id: str,
    caller_number: str,
    duration_seconds: int,
    ai_summary: Optional[str] = None,
) -> Optional[str]:
    """
    Log incoming call event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        call_id: Call UUID
        caller_number: Phone number of caller
        duration_seconds: Call duration in seconds
        ai_summary: AI-generated summary of call

    Returns:
        Activity ID or None
    """
    meta = {
        "caller_number": caller_number,
        "duration_seconds": duration_seconds,
        "summary": ai_summary,
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="call_logged",
        title=f"Incoming call from {caller_number}",
        entity_type="call",
        entity_id=call_id,
        meta=meta,
    )


async def log_payment_received(
    conn,
    clinic_id: str,
    payment_id: str,
    appointment_id: str,
    amount: float,
    currency: str = "INR",
    payment_method: str = "razorpay",
    patient_name: Optional[str] = None,
) -> Optional[str]:
    """
    Log successful payment event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        payment_id: Payment UUID
        appointment_id: Associated appointment UUID
        amount: Payment amount
        currency: Currency code (default: 'INR')
        payment_method: Payment method (default: 'razorpay')
        patient_name: Name of patient (optional)

    Returns:
        Activity ID or None
    """
    meta = {
        "appointment_id": appointment_id,
        "amount": amount,
        "currency": currency,
        "payment_method": payment_method,
        "patient_name": patient_name,
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="payment_received",
        title=f"Payment received: {amount} {currency}",
        entity_type="payment",
        entity_id=payment_id,
        meta=meta,
    )


async def log_patient_created(
    conn,
    clinic_id: str,
    patient_id: str,
    patient_name: str,
    phone_number: str,
    email: Optional[str] = None,
) -> Optional[str]:
    """
    Log new patient registration event.

    Args:
        conn: Database connection
        clinic_id: Clinic UUID
        patient_id: Patient UUID
        patient_name: Name of patient
        phone_number: Phone number
        email: Email address (optional)

    Returns:
        Activity ID or None
    """
    meta = {
        "patient_name": patient_name,
        "phone_number": phone_number,
        "email": email,
    }

    return await log_activity(
        conn,
        clinic_id=clinic_id,
        event_type="patient_created",
        title=f"New patient registered: {patient_name}",
        entity_type="patient",
        entity_id=patient_id,
        meta=meta,
    )
