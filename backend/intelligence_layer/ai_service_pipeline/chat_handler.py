import asyncio
import json
from utils import stream_agent_chat, WebSocketSingleton, WebSocketResponse
from .client_ai_service_need_profiler import client_ai_service_need_profiler
from .client_ai_service_deployer import client_ai_service_deployer
import logging


from .constants import (
    STEP_SERVICE_NEED_PROFILING,
    STEP_SERVICE_DEPLOYMENT,
    STEP_NETWORK_ADAPTATION,
    STEP_SERVICE_MONITORING,
)

logger = logging.getLogger(__name__)


async def handle_ai_service_pipeline_chat(
    websocket, simulation_engine, knowledge_router, data
):
    current_step = data.get("current_step", None)
    messages = data.get("messages", [])

    if current_step is None:
        logger.error("Current step is not provided in the data.")
        return
    if not messages:
        logger.error("Messages cannot be empty.")

    logger.info(f"Handling AI service pipeline step {current_step}")
    logger.info(f"Last message: {messages[-1]}")

    if current_step == STEP_SERVICE_NEED_PROFILING:
        await stream_agent_chat(
            websocket=websocket,
            simulation_engine=simulation_engine,
            knowledge_router=knowledge_router,
            data=messages,
            command="ai_service_pipeline_response",
            agent_func=client_ai_service_need_profiler,
        )
    elif current_step == STEP_SERVICE_DEPLOYMENT:
        await stream_agent_chat(
            websocket=websocket,
            simulation_engine=simulation_engine,
            knowledge_router=knowledge_router,
            data=messages,
            command="ai_service_pipeline_response",
            agent_func=client_ai_service_deployer,
        )
    elif current_step == "step_test_ai_service_deployment":
        logger.info("Testing AI service deployment step.")
        logger.info(f"Data received: {data}")
        ai_service_request = data.get("data", None)

        if ai_service_request is None:
            logger.error("AI service request data is missing.")
            return
        logger.info(f"AI service request: {ai_service_request}")

        ai_service_name = ai_service_request["ai_service_name"]
        ue_id_list = ai_service_request["ue_id_list"]
        ai_service_data = knowledge_router.query_knowledge(
            f"/ai_services/{ai_service_name}/raw",
        )
        if not ai_service_data:
            logger.error(f"AI service {ai_service_name} is not supported.")
            return

        new_ai_service_subscription = (
            simulation_engine.ric.ai_service_subscription_manager.create_subscription(
                ai_service_name=ai_service_name,
                ai_service_data=ai_service_data,
                ue_id_list=ue_id_list,
            )
        )

        if new_ai_service_subscription is None:
            logger.error("Failed to create AI service subscription.")
            return

        logger.info(
            f"AI service subscription created successfully with ID: {new_ai_service_subscription.subscription_id} for service {ai_service_name} with UEs {ue_id_list}."
        )

        response_message = f"""AI service subscription created successfully with ID: {new_ai_service_subscription.subscription_id} for service {ai_service_name} with UEs {ue_ids}.

    When the User Equipments are connected to any base station,
    the AI service will be started automatically at the edge clustered of that base station if computation resources are available.

    The AI services offers RESTful API endspoint at `http://cranfield_6G.com/ai_services/{ai_service_name}`. 
    The Base Stations will automatically perform local breakout of UE's requests and route them to the AI services at the edge clusters.

    Below are the sample code snippets for using the AI service:

    ```python
    {ai_service_data["code"]["ai_client_script_content"]}
    ```
    """
        response = WebSocketResponse(
            layer="intelligence_layer",
            command="ai_service_pipeline_response",
            response={
                "event_type": "ai_service_deployment",
                "subscription_data": new_ai_service_subscription.to_json(),
                "message": response_message,
            },
            error=None,
        )
        await websocket.send(response.to_json())
    else:
        logger.error(f"Unsupported step: {current_step}")
