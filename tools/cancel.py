# #tools/cancel.py
# from pipecat.services.llm_service import FunctionCallParams
# from tools.pool import get_pool

# async def cancel_appointment(params: FunctionCallParams, appointment_id: str):
#     """Cancel an existing appointment.
#     Args:
#         appointment_id: The UUID of the appointment to cancel.
#     """
#     pool = get_pool()
#     await pool.execute("UPDATE appointments SET status='cancelled' WHERE id=$1", appointment_id)
#     await params.result_callback({"status": "success"})