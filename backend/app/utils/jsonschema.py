from pydantic import BaseModel, ValidationError


async def validate_or_repair(raw: dict, model: type[BaseModel], repair_cb):
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        repaired = await repair_cb(raw, model.model_json_schema(), exc.errors())
        return model.model_validate(repaired)
