def validate_schema(endpoint_data):
    errors = []

    if not isinstance(endpoint_data, dict):
        return {"valid": False, "errors": ["Data must be a dictionary"]}

    required_fields = ["name", "description", "method", "endpoint"]
    for field in required_fields:
        if field not in endpoint_data:
            errors.append(f"Missing required field: {field}")
        elif not isinstance(endpoint_data[field], str):
            errors.append(f"Field '{field}' must be a string")
        elif not endpoint_data[field].strip():
            errors.append(f"Field '{field}' cannot be empty")

    if "headers" in endpoint_data:
        headers = endpoint_data["headers"]
        if not isinstance(headers, list):
            errors.append("Headers must be an array")
        else:
            for i, header in enumerate(headers):
                if not isinstance(header, dict):
                    errors.append(f"Header at index {i} must be an object")
                    continue

                if "name" not in header:
                    errors.append(f"Header at index {i} missing required 'name' field")
                elif not isinstance(header["name"], str):
                    errors.append(f"Header at index {i} 'name' must be a string")

                if "required" in header and not isinstance(header["required"], bool):
                    errors.append(f"Header at index {i} 'required' must be a boolean")

                allowed_header_fields = {"name", "required"}
                for field in header:
                    if field not in allowed_header_fields:
                        errors.append(
                            f"Header at index {i} has unexpected field: {field}"
                        )

    schema_fields = ["parameters", "body"]
    for field in schema_fields:
        if field in endpoint_data:
            schema_obj = endpoint_data[field]
            if schema_obj:
                schema_errors = _validate_schema_object(schema_obj, field)
                errors.extend(schema_errors)

    return {"valid": len(errors) == 0, "errors": errors}


def _validate_schema_object(schema_obj, field_name):
    errors = []

    if not isinstance(schema_obj, dict):
        errors.append(f"'{field_name}' must be an object")
        return errors

    if "type" not in schema_obj:
        errors.append(f"'{field_name}' missing required 'type' field")
    elif not isinstance(schema_obj["type"], str):
        errors.append(f"'{field_name}' 'type' must be a string")

    if "properties" not in schema_obj:
        errors.append(f"'{field_name}' missing required 'properties' field")
    elif not isinstance(schema_obj["properties"], dict):
        errors.append(f"'{field_name}' 'properties' must be an object")
    else:
        for prop_name, prop_schema in schema_obj["properties"].items():
            prop_errors = _validate_nested_property(
                prop_schema, f"{field_name}.properties.{prop_name}"
            )
            errors.extend(prop_errors)

    if "description" in schema_obj and not isinstance(schema_obj["description"], str):
        errors.append(f"'{field_name}' 'description' must be a string")

    allowed_fields = {"type", "properties", "description", "required"}
    for field in schema_obj:
        if field not in allowed_fields:
            errors.append(f"'{field_name}' has unexpected field: {field}")

    return errors


def _validate_nested_property(prop_schema, prop_path):
    errors = []

    if not isinstance(prop_schema, dict):
        errors.append(f"Property '{prop_path}' must be an object")
        return errors

    if "type" not in prop_schema:
        errors.append(f"Property '{prop_path}' missing required 'type' field")
    elif not isinstance(prop_schema["type"], str):
        errors.append(f"Property '{prop_path}' 'type' must be a string")

    if "description" in prop_schema and not isinstance(prop_schema["description"], str):
        errors.append(f"Property '{prop_path}' 'description' must be a string")

    if prop_schema.get("type") == "object" and "properties" in prop_schema:
        if not isinstance(prop_schema["properties"], dict):
            errors.append(f"Property '{prop_path}' 'properties' must be an object")
        else:
            for nested_name, nested_schema in prop_schema["properties"].items():
                nested_errors = _validate_nested_property(
                    nested_schema, f"{prop_path}.{nested_name}"
                )
                errors.extend(nested_errors)

    if prop_schema.get("type") == "array" and "items" in prop_schema:
        items_errors = _validate_nested_property(
            prop_schema["items"], f"{prop_path}.items"
        )
        errors.extend(items_errors)

    return errors
