import fastapi
import sqlalchemy.orm
from fastapi.concurrency import run_in_threadpool

import mlrun.common.schemas
from mlrun.runtimes import RuntimeKinds
from mlrun.utils import logger

import services.api.crud.model_monitoring.deployment as mm_deployment


async def start_model_endpoint_creation_background_task(
    project: str,
    name: str,
    background_tasks: fastapi.BackgroundTasks,
    function: dict,
    db_session: sqlalchemy.orm.Session,
):
    returned_background_tasks = mlrun.common.schemas.BackgroundTaskList(
        background_tasks=[]
    )
    kind = function.get("kind")
    logger.info(
        "111 Deploying function",
        project=project,
        name=name,
        kind=kind,
        serving_spec=str(function["spec"].get("serving_spec")),
    )
    if (
        kind == RuntimeKinds.serving
        or kind == RuntimeKinds.job
        and function["spec"].get("serving_spec")
    ):
        monitoring_deployment = mm_deployment.MonitoringDeployment(project=project)
        (
            model_endpoints_instructions,
            function,
        ) = await monitoring_deployment._create_model_endpoints_instructions(
            db_session=db_session,
            function=function,
            function_name=name,
            project=project,
        )
        logger.info(
            "Creating Background Task for model endpoints creation",
            project=project,
            function=name,
        )
        returned_background_task = await run_in_threadpool(
            monitoring_deployment._create_model_endpoint_background_task,
            db_session=db_session,
            background_tasks=background_tasks,
            project_name=project,
            function_name=name,
            function_tag=function.get("metadata", {}).get("tag") or "latest",
            model_endpoints_instructions=model_endpoints_instructions,
        )
        returned_background_tasks.background_tasks.append(returned_background_task)

    model_endpoint_creation_task_name = (
        returned_background_tasks.background_tasks[0].metadata.name
        if returned_background_tasks.background_tasks
        else None
    )

    return model_endpoint_creation_task_name, returned_background_tasks
