#tools/faq.py
# tools/faq.py
import os
from loguru import logger
import google.generativeai as genai
from pipecat.services.llm_service import FunctionCallParams
from tools.pool import get_pool 

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

async def query_clinic_faq(params: FunctionCallParams, user_question: str):
    """Tool to search the clinic's official documents for answers."""
    logger.info(f"🔍 FAQ Lookup: {user_question}")
    
    try:
        # Convert user question to 768-dim vector
        response = genai.embed_content(
            model="models/gemini-embedding-001",
            content=user_question,
            task_type="retrieval_query",
            output_dimensionality=768
        )
        question_embedding = response['embedding']
        
        pool = get_pool()
        async with pool.acquire() as conn:
            # 👇 FIXED: Changed LIMIT 2 to LIMIT 1
            query = """
                SELECT chunk_text 
                FROM clinic_knowledge 
                ORDER BY embedding <-> $1::vector 
                LIMIT 1
            """
            results = await conn.fetch(query, str(question_embedding))
            
            if not results:
                await params.result_callback({
                    "status": "success",
                    "message": "SYSTEM DIRECTIVE: Tell the user you don't have that specific info and offer to connect them to the front desk."
                })
                return

            context = " ".join([r['chunk_text'] for r in results])
            
            # 👇 FIXED: Updated prompt to strictly answer ONLY the question asked
            await params.result_callback({
                "status": "success",
                "context": context,
                "message": f"SYSTEM DIRECTIVE: Answer the user's question using ONLY the relevant information from this context: '{context}'. DO NOT mention unrelated topics. Be brief (1-2 sentences)."
            })
            
    except Exception as e:
        logger.error(f"❌ FAQ Error: {e}")
        await params.result_callback({"status": "error", "message": "SYSTEM DIRECTIVE: Apologize for the technical glitch."})