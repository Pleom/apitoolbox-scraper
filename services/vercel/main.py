from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
import requests 
import yaml
import re
from ..validate_schema import validate_schema
import os
import json
from collections import defaultdict


def get_docs_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://vercel.com/docs/rest-api/reference/endpoints/")
        page.wait_for_selector("li[data-group-tag]")
        data_groups = page.query_selector_all("li[data-group-tag]")
        for group in data_groups:
            group.click()
            time.sleep(1)

        html = page.content()

        browser.close()

    return html


def get_endpoints_html(html):
    soup = BeautifulSoup(html, "html.parser")
    endpoints = []

    for link in soup.select("li[data-group-tag] a"):
        href = link.get("href")
        if href:
            full_url = f"https://vercel.com{href}.md"
            endpoints.append(full_url)

    return endpoints


def parse_yaml_schema_to_json_schema(yaml_schema):
    if not yaml_schema:
        return {}

    def convert_schema_item(item):
        if isinstance(item, dict):
            schema = {}

            if "type" in item:
                schema["type"] = item["type"]

            if "description" in item:
                schema["description"] = item["description"]

            if "properties" in item:
                schema["type"] = "object"
                schema["properties"] = {}
                for prop, prop_schema in item["properties"].items():
                    schema["properties"][prop] = convert_schema_item(prop_schema)

            if "items" in item:
                schema["type"] = "array"
                schema["items"] = convert_schema_item(item["items"])

            if "required" in item or "requiredProperties" in item:
                schema["required"] = item.get(
                    "required", item.get("requiredProperties", [])
                )

            if "allOf" in item:
                merged = {"type": "object", "properties": {}}
                required_fields = []

                for sub_item in item["allOf"]:
                    sub_schema = convert_schema_item(sub_item)
                    if "type" in sub_schema and sub_schema["type"] != "object":
                        merged["type"] = sub_schema["type"]
                    if "description" in sub_schema:
                        merged["description"] = sub_schema["description"]
                    if "properties" in sub_schema:
                        merged["properties"].update(sub_schema["properties"])
                    if "required" in sub_schema:
                        required_fields.extend(sub_schema["required"])

                if required_fields:
                    merged["required"] = list(set(required_fields))
                if not merged["properties"]:
                    merged.pop("properties", None)
                    if merged["type"] == "object":
                        merged["type"] = "string"

                return merged

            return schema
        elif isinstance(item, list) and len(item) > 0:
            return convert_schema_item(item[0])

        return {"type": "string"}

    return convert_schema_item(yaml_schema)


def get_endpoint_details(endpoint_url):
    try:
        response = requests.get(endpoint_url)
        response.raise_for_status()
        md_content = response.text

        name_match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else ""

        desc_match = re.search(r"^>\s+(.+)$", md_content, re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else ""

        yaml_match = re.search(r"```yaml.*?\n(.*?)\n```", md_content, re.DOTALL)
        if not yaml_match:
            return {
                "name": name,
                "description": description,
                "method": "",
                "endpoint": "",
                "headers": [],
                "parameters": {},
                "body": {},
                "response": {},
            }

        yaml_content = yaml_match.group(1)
        try:
            yaml_data = yaml.safe_load(yaml_content)
        except yaml.YAMLError:
            return {"error": "Failed to parse YAML"}

        paths_data = yaml_data.get("paths", {})
        method = paths_data.get("method", "").upper()
        path = paths_data.get("path", "")

        servers = paths_data.get("servers", [])
        base_url = servers[0]["url"] if servers else "https://api.vercel.com"

        endpoint = f"{base_url}{path}"
        if "{" in path:
            pass
        else:
            endpoint = re.sub(r":(\w+)", r"{\1}", endpoint)

        headers = []
        request_data = paths_data.get("request", {})
        security = request_data.get("security", [])
        for sec in security:
            if "parameters" in sec and "header" in sec["parameters"]:
                for header_name, header_info in sec["parameters"]["header"].items():
                    headers.append({"name": header_name, "required": True})

        parameters = {"type": "object", "properties": {}, "required": []}

        params = request_data.get("parameters", {})

        if "path" in params:
            for param_name, param_info in params["path"].items():
                schema = param_info.get("schema", [{}])
                if isinstance(schema, list) and len(schema) > 0:
                    param_schema = schema[0]
                else:
                    param_schema = schema

                converted_schema = parse_yaml_schema_to_json_schema(param_schema)
                parameters["properties"][param_name] = {
                    "type": converted_schema.get("type", "string"),
                    "description": converted_schema.get("description", ""),
                }

                if param_schema.get("required", False):
                    parameters["required"].append(param_name)

        if "query" in params:
            for param_name, param_info in params["query"].items():
                schema = param_info.get("schema", [{}])
                if isinstance(schema, list) and len(schema) > 0:
                    param_schema = schema[0]
                else:
                    param_schema = schema

                converted_schema = parse_yaml_schema_to_json_schema(param_schema)
                parameters["properties"][param_name] = {
                    "type": converted_schema.get("type", "string"),
                    "description": converted_schema.get("description", ""),
                }

        if not parameters["properties"]:
            parameters = {}
        elif not parameters["required"]:
            parameters.pop("required")

        body = {}
        if "body" in request_data and request_data["body"]:
            body = parse_yaml_schema_to_json_schema(request_data["body"])
            if body:
                clean_body = {}
                if "type" in body:
                    clean_body["type"] = body["type"]
                if "description" in body:
                    clean_body["description"] = body["description"]
                if "properties" in body:
                    clean_body["properties"] = body["properties"]
                if "required" in body:
                    clean_body["required"] = body["required"]
                body = clean_body

        response_data = {}
        responses = paths_data.get("response", {})
        if "200" in responses and "application/json" in responses["200"]:
            json_response = responses["200"]["application/json"]
            schema_array = json_response.get("schemaArray", [])
            if schema_array:
                response_schema = parse_yaml_schema_to_json_schema(schema_array[0])
                if response_schema:
                    clean_response = {}
                    if "type" in response_schema:
                        clean_response["type"] = response_schema["type"]
                    if "description" in response_schema:
                        clean_response["description"] = response_schema["description"]
                    if "properties" in response_schema:
                        clean_response["properties"] = response_schema["properties"]
                    response_data = clean_response

        return {
            "name": name,
            "description": description,
            "method": method,
            "endpoint": endpoint,
            "headers": headers,
            "parameters": parameters,
            "body": body,
            "response": response_data,
        }

    except Exception as e:
        return {"error": f"Failed to process endpoint: {str(e)}"}


def extract_group_from_endpoint(endpoint_url):
    """Extract the group name from the endpoint URL"""
    # Example: https://vercel.com/docs/rest-api/reference/endpoints/access-groups/reads-an-access-group.md
    # Should return: access-groups
    match = re.search(r'/endpoints/([^/]+)/', endpoint_url)
    if match:
        return match.group(1)
    return "unknown"


def ensure_output_directory():
    """Ensure the output directory exists"""
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    return output_dir


def save_main_page_json(output_dir, groups):
    """Save the main page.json with all tool groups"""
    main_page_data = {
        "name": "vercel",
        "version": "1",
        "tools": [{"name": group} for group in sorted(groups)]
    }
    
    main_page_path = os.path.join(output_dir, "page.json")
    with open(main_page_path, "w", encoding="utf-8") as f:
        json.dump(main_page_data, f, indent=2)
    
    print(f"Saved main page.json with {len(groups)} groups")


def save_group_page_json(output_dir, group_name, endpoints_data):
    """Save the group-specific page.json with all endpoints for that group"""
    group_dir = os.path.join(output_dir, group_name)
    if not os.path.exists(group_dir):
        os.makedirs(group_dir)
    
    group_page_data = {
        "name": group_name,
        "tools": endpoints_data
    }
    
    group_page_path = os.path.join(group_dir, "page.json")
    with open(group_page_path, "w", encoding="utf-8") as f:
        json.dump(group_page_data, f, indent=2)
    
    print(f"Saved {group_name}/page.json with {len(endpoints_data)} endpoints")


def main():
    html = get_docs_html()
   
    
    endpoints = get_endpoints_html(html)

    
    # Group endpoints by their category
    grouped_endpoints = defaultdict(list)
    
    # Process each endpoint
    for endpoint in endpoints:
        # print(f"Processing: {endpoint}")
        
        # Extract group name from endpoint URL
        group_name = extract_group_from_endpoint(endpoint)
        
        # Get endpoint details
        endpoint_details = get_endpoint_details(endpoint)
        
        # Validate the schema
        validation_result = validate_schema(endpoint_details)
        if not validation_result["valid"]:
            print(f"Validation failed for {endpoint}: {validation_result}")
            continue
        
        # Add to the appropriate group
        grouped_endpoints[group_name].append(endpoint_details)
        # print(f"Successfully processed endpoint for group: {group_name}")
    
    # Ensure output directory exists
    output_dir = ensure_output_directory()
    
    # Save main page.json with all groups
    all_groups = list(grouped_endpoints.keys())
    save_main_page_json(output_dir, all_groups)
    
    # Save individual group page.json files
    for group_name, endpoints_data in grouped_endpoints.items():
        save_group_page_json(output_dir, group_name, endpoints_data)
    
    print(f"\nCompleted processing {len(endpoints)} endpoints across {len(all_groups)} groups")
    print(f"Output saved to: {output_dir}")

if __name__ == "__main__":
    main()
