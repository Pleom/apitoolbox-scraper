import requests
import json
import copy
import hashlib
import re
from typing import Dict, List, Any, Optional, Set
import os
import shutil
import argparse

def hash_url(url: str) -> str:
    """Generate a hash of the URL for file naming"""
    return hashlib.md5(url.encode()).hexdigest()

def to_camel_case(text: str) -> str:
    """
    Convert a string to camelCase format
    Example: 'security-advisories/get-global-advisory' -> 'advisoriesGetGlobalAdvisory'
    """
    # First, replace any non-alphanumeric characters with spaces
    text = re.sub(r'[^a-zA-Z0-9]', ' ', text)
    
    # Split by whitespace
    words = text.split()
    
    # Convert to camelCase
    if not words:
        return ''
    
    # First word starts with lowercase
    result = words[0].lower()
    
    # Subsequent words start with uppercase
    for word in words[1:]:
        if word:
            result += word[0].upper() + word[1:].lower()
    
    return result

def create_directory(directory_path):
    """Create directory if it doesn't exist"""
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Created directory: {directory_path}")

def convert_swagger_to_openapi(swagger_url, force_convert=False):
    """Convert a Swagger spec to OpenAPI 3.0 with file caching based on URL hash"""
    # Generate hash for the URL
    url_hash = hash_url(swagger_url)
    output_file = f"{url_hash}.json"
    
    # Check if the file already exists
    if os.path.exists(output_file) and not force_convert:
        print(f"Using cached OpenAPI spec from {output_file}")
        return output_file
    
    try:
        # Fetch the Swagger spec
        swagger_response = requests.get(swagger_url)
        swagger_response.raise_for_status()
        
        if swagger_url.lower().endswith('.yaml') or swagger_url.lower().endswith('.yml'):
            import yaml
            swagger_json = yaml.safe_load(swagger_response.text)
        else:
            swagger_json = swagger_response.json()
        
        # Send it to the Swagger Converter API
        convert_response = requests.post(
            "https://converter.swagger.io/api/convert",
            headers={"Content-Type": "application/json"},
            json=swagger_json
        )
        convert_response.raise_for_status()
        openapi_json = convert_response.json()

        # Save to output file
        with open(output_file, "w") as f:
            json.dump(openapi_json, f, indent=2)

        print(f"OpenAPI 3.0 spec saved to {output_file}")
        return output_file

    except requests.exceptions.RequestException as e:
        print("Request failed:", e)
        return None
    except ValueError:
        print("Invalid JSON in Swagger spec or conversion output.")
        return None

class OpenAPIPathExtractor:
    def __init__(self, openapi_spec: Dict[str, Any], base_url: str = None):
        self.spec = openapi_spec
        self.components = openapi_spec.get('components', {})
        self.resolved_refs = {}  # Cache for resolved references
        self.resolving_refs = set()  # Track currently resolving references to detect cycles
        self.base_url = base_url  # Store base URL override
    
    def resolve_reference(self, ref_path: str, visited: Optional[Set[str]] = None) -> Any:
        """
        Resolve a $ref pointer to its actual value with circular reference detection
        """
        if visited is None:
            visited = set()
            
        if ref_path in visited:
            # Circular reference detected - return a placeholder
            return {"type": "object", "description": f"Circular reference to {ref_path}"}
        
        if ref_path in self.resolved_refs:
            return self.resolved_refs[ref_path]
        
        if not ref_path.startswith('#/'):
            raise ValueError(f"Only internal references supported: {ref_path}")
        
        # Add to visited set
        new_visited = visited.copy()
        new_visited.add(ref_path)
        
        # Remove '#/' and split by '/'
        path_parts = ref_path[2:].split('/')
        
        # Navigate through the spec
        current = self.spec
        try:
            for part in path_parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    raise ValueError(f"Reference not found: {ref_path}")
        except Exception as e:
            print(f"Warning: Could not resolve reference {ref_path}: {e}")
            return {"type": "object", "description": f"Unresolved reference: {ref_path}"}
        
        # Dereference the resolved object
        resolved = self.dereference_object(current, new_visited)
        self.resolved_refs[ref_path] = resolved
        return resolved
    
    def dereference_object(self, obj: Any, visited: Optional[Set[str]] = None, depth: int = 0) -> Any:
        """
        Recursively dereference all $ref pointers in an object with depth limit
        """
        if depth > 50:  # Prevent infinite recursion
            return obj
            
        if visited is None:
            visited = set()
            
        if isinstance(obj, dict):
            if '$ref' in obj:
                # Resolve the reference
                try:
                    resolved = self.resolve_reference(obj['$ref'], visited)
                    return resolved
                except Exception as e:
                    print(f"Warning: Failed to resolve reference {obj['$ref']}: {e}")
                    return {"type": "object", "description": f"Failed to resolve: {obj['$ref']}"}
            else:
                # Recursively process all values in the dictionary
                result = {}
                for key, value in obj.items():
                    try:
                        result[key] = self.dereference_object(value, visited, depth + 1)
                    except Exception as e:
                        print(f"Warning: Error processing key '{key}': {e}")
                        result[key] = value
                return result
        elif isinstance(obj, list):
            # Recursively process all items in the list
            result = []
            for i, item in enumerate(obj):
                try:
                    result.append(self.dereference_object(item, visited, depth + 1))
                except Exception as e:
                    print(f"Warning: Error processing list item {i}: {e}")
                    result.append(item)
            return result
        else:
            # Return primitive values as-is
            return obj
    
    def safe_get(self, obj: Any, key: str, default: Any = None) -> Any:
        """
        Safely get a value from an object
        """
        try:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return default
        except:
            return default
    
    def extract_parameters(self, parameters: List[Dict]) -> List[Dict]:
        """
        Extract and format parameters with error handling
        """
        if not parameters:
            return []
        
        param_list = []
        for i, param in enumerate(parameters):
            try:
                dereferenced_param = self.dereference_object(param)
                param_info = {
                    'name': self.safe_get(dereferenced_param, 'name', f'param_{i}'),
                    'in': self.safe_get(dereferenced_param, 'in', 'query'),
                    'description': self.safe_get(dereferenced_param, 'description', ''),
                    'required': self.safe_get(dereferenced_param, 'required', False),
                    'schema': self.safe_get(dereferenced_param, 'schema', {}),
                    'style': self.safe_get(dereferenced_param, 'style', ''),
                    'explode': self.safe_get(dereferenced_param, 'explode', False)
                }
                param_list.append(param_info)
            except Exception as e:
                print(f"Warning: Error processing parameter {i}: {e}")
                # Add a fallback parameter
                param_list.append({
                    'name': f'param_{i}',
                    'in': 'query',
                    'description': f'Error processing parameter: {e}',
                    'required': False,
                    'schema': {},
                    'style': '',
                    'explode': False
                })
        
        return param_list
    
    def convert_parameters_to_json_schema(self, parameters: List[Dict]) -> Dict:
        """
        Convert OpenAPI parameters to JSON schema format for LLMs
        """
        if not parameters:
            return {}
        
        try:
            properties = {}
            required = []
            
            for param in parameters:
                try:
                    dereferenced_param = self.dereference_object(param)
                    param_name = self.safe_get(dereferenced_param, 'name', '')
                    param_schema = self.safe_get(dereferenced_param, 'schema', {})
                    param_description = self.safe_get(dereferenced_param, 'description', '')
                    param_required = self.safe_get(dereferenced_param, 'required', False)
                    
                    if param_name:
                        # Convert OpenAPI schema to JSON schema
                        json_schema_prop = self.convert_openapi_schema_to_json_schema(param_schema)
                        if param_description:
                            json_schema_prop['description'] = param_description
                        
                        properties[param_name] = json_schema_prop
                        
                        if param_required:
                            required.append(param_name)
                            
                except Exception as e:
                    print(f"Warning: Error converting parameter to JSON schema: {e}")
                    continue
            
            # If no valid parameters were processed, return empty object
            if not properties:
                return {}
            
            return {
                "type": "object",
                "properties": properties,
                "required": required
            }
            
        except Exception as e:
            print(f"Warning: Error converting parameters to JSON schema: {e}")
            return {}
    
    def convert_openapi_schema_to_json_schema(self, schema: Dict) -> Dict:
        """
        Convert OpenAPI schema to JSON schema format
        """
        if not isinstance(schema, dict):
            return {"type": "string"}
        
        try:
            # Start with a copy of the schema
            json_schema = {}
            
            # Handle basic type - but infer from properties if not specified
            schema_type = self.safe_get(schema, 'type')
            
            # If no type specified, infer from schema structure
            if not schema_type:
                if 'properties' in schema or 'additionalProperties' in schema:
                    schema_type = 'object'
                elif 'items' in schema:
                    schema_type = 'array'
                elif 'enum' in schema:
                    # Keep as untyped for enum, or infer from enum values
                    enum_values = schema.get('enum', [])
                    if enum_values:
                        first_val = enum_values[0]
                        if isinstance(first_val, str):
                            schema_type = 'string'
                        elif isinstance(first_val, (int, float)):
                            schema_type = 'number'
                        elif isinstance(first_val, bool):
                            schema_type = 'boolean'
                else:
                    schema_type = 'string'  # default fallback
            
            json_schema['type'] = schema_type
            
            # Handle description
            description = self.safe_get(schema, 'description')
            if description:
                json_schema['description'] = description
            
            # Handle enum
            enum_values = self.safe_get(schema, 'enum')
            if enum_values:
                json_schema['enum'] = enum_values
            
            # Handle format
            format_value = self.safe_get(schema, 'format')
            if format_value:
                json_schema['format'] = format_value
            
            # Handle array items
            if schema_type == 'array':
                items = self.safe_get(schema, 'items', {})
                json_schema['items'] = self.convert_openapi_schema_to_json_schema(items)
            
            # Handle object properties
            elif schema_type == 'object':
                properties = self.safe_get(schema, 'properties', {})
                if properties:
                    json_schema['properties'] = {}
                    for prop_name, prop_schema in properties.items():
                        json_schema['properties'][prop_name] = self.convert_openapi_schema_to_json_schema(prop_schema)
                
                required = self.safe_get(schema, 'required', [])
                if required:
                    json_schema['required'] = required
                
                # Handle additionalProperties
                additional_props = self.safe_get(schema, 'additionalProperties')
                if additional_props is not None:
                    if isinstance(additional_props, dict):
                        json_schema['additionalProperties'] = self.convert_openapi_schema_to_json_schema(additional_props)
                    else:
                        json_schema['additionalProperties'] = additional_props
            
            # Handle additional constraints
            for constraint in ['minimum', 'maximum', 'minLength', 'maxLength', 'pattern', 'default', 'example']:
                value = self.safe_get(schema, constraint)
                if value is not None:
                    json_schema[constraint] = value
            
            return json_schema
            
        except Exception as e:
            print(f"Warning: Error converting OpenAPI schema to JSON schema: {e}")
            return {"type": "string"}
    
    def extract_request_body(self, request_body: Dict) -> Dict:
        """
        Extract and format request body information with error handling
        """
        if not request_body:
            return {}
        
        try:
            dereferenced_body = self.dereference_object(request_body)
            
            body_info = {
                'description': self.safe_get(dereferenced_body, 'description', ''),
                'required': self.safe_get(dereferenced_body, 'required', False),
                'content': {}
            }
            
            content = self.safe_get(dereferenced_body, 'content', {})
            if isinstance(content, dict):
                for media_type, media_info in content.items():
                    if isinstance(media_info, dict):
                        body_info['content'][media_type] = {
                            'schema': self.safe_get(media_info, 'schema', {}),
                            'examples': self.safe_get(media_info, 'examples', {}),
                            'example': self.safe_get(media_info, 'example', None)
                        }
            
            return body_info
        except Exception as e:
            print(f"Warning: Error processing request body: {e}")
            return {'description': f'Error processing request body: {e}', 'required': False, 'content': {}}
    
    def convert_request_body_to_json_schema(self, request_body: Dict) -> Dict:
        """
        Convert OpenAPI request body to JSON schema format for LLMs
        """
        if not request_body:
            return {}
        
        try:
            dereferenced_body = self.dereference_object(request_body)
            content = self.safe_get(dereferenced_body, 'content', {})
            
            # Look for JSON content first, then any other content type
            schema = {}
            for media_type in ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']:
                if media_type in content:
                    media_info = content[media_type]
                    schema = self.safe_get(media_info, 'schema', {})
                    break
            
            # If no preferred media type found, use the first available
            if not schema and content:
                first_media = list(content.keys())[0]
                media_info = content[first_media]
                schema = self.safe_get(media_info, 'schema', {})
            
            if not schema:
                return {}
            
            return self.convert_openapi_schema_to_json_schema(schema)
            
        except Exception as e:
            print(f"Warning: Error converting request body to JSON schema: {e}")
            return {}
    
    def extract_responses(self, responses: Dict) -> Dict:
        """
        Extract and format response information with error handling
        """
        if not responses:
            return {}
        
        try:
            dereferenced_responses = self.dereference_object(responses)
            
            response_info = {}
            if isinstance(dereferenced_responses, dict):
                for status_code, response_data in dereferenced_responses.items():
                    if isinstance(response_data, dict):
                        response_info[status_code] = {
                            'description': self.safe_get(response_data, 'description', ''),
                            'headers': self.safe_get(response_data, 'headers', {}),
                            'content': {}
                        }
                        
                        content = self.safe_get(response_data, 'content', {})
                        if isinstance(content, dict):
                            for media_type, media_info in content.items():
                                if isinstance(media_info, dict):
                                    response_info[status_code]['content'][media_type] = {
                                        'schema': self.safe_get(media_info, 'schema', {}),
                                        'examples': self.safe_get(media_info, 'examples', {}),
                                        'example': self.safe_get(media_info, 'example', None)
                                    }
            
            return response_info
        except Exception as e:
            print(f"Warning: Error processing responses: {e}")
            return {'default': {'description': f'Error processing responses: {e}', 'headers': {}, 'content': {}}}
    
    def convert_response_to_json_schema(self, responses: Dict) -> Dict:
        """
        Convert OpenAPI responses to JSON schema format for LLMs
        Checks response codes from 200 to 299 in order until one is found
        """
        if not responses:
            return {}
        
        try:
            dereferenced_responses = self.dereference_object(responses)
            
            # Check response codes from 200 to 299 in order
            response_data = None
            for status_code in range(200, 300):
                status_str = str(status_code)
                if status_str in dereferenced_responses:
                    response_data = dereferenced_responses[status_str]
                    break
            
            if not response_data:
                return {}
            
            content = self.safe_get(response_data, 'content', {})
            
            # Look for JSON content first, then any other content type
            schema = {}
            for media_type in ['application/json', 'text/plain', 'application/xml']:
                if media_type in content:
                    media_info = content[media_type]
                    schema = self.safe_get(media_info, 'schema', {})
                    break
            
            # If no preferred media type found, use the first available
            if not schema and content:
                first_media = list(content.keys())[0]
                media_info = content[first_media]
                schema = self.safe_get(media_info, 'schema', {})
            
            if not schema:
                return {}
            
            return self.convert_openapi_schema_to_json_schema(schema)
            
        except Exception as e:
            print(f"Warning: Error converting response to JSON schema: {e}")
            return {}
    
    def extract_headers(self, operation: Dict) -> List[Dict]:
        """
        Extract headers from parameters with error handling
        """
        try:
            parameters = self.safe_get(operation, 'parameters', [])
            headers = []
            
            if isinstance(parameters, list):
                for param in parameters:
                    try:
                        dereferenced_param = self.dereference_object(param)
                        if self.safe_get(dereferenced_param, 'in') == 'header':
                            header_info = {
                                'name': self.safe_get(dereferenced_param, 'name', ''),
                                'description': self.safe_get(dereferenced_param, 'description', ''),
                                'required': self.safe_get(dereferenced_param, 'required', False),
                                'schema': self.safe_get(dereferenced_param, 'schema', {})
                            }
                            headers.append(header_info)
                    except Exception as e:
                        print(f"Warning: Error processing header parameter: {e}")
            
            return headers
        except Exception as e:
            print(f"Warning: Error extracting headers: {e}")
            return []
    
    def extract_servers(self, path_item: Dict = None, operation: Dict = None) -> List[Dict]:
        """
        Extract server information with error handling
        Servers can be defined at root level, path level, or operation level
        Operation level takes precedence over path level, path level over root level
        If base_url is provided, it overrides all server URLs
        """
        try:
            servers = []
            
            # Start with root level servers (lowest priority)
            root_servers = self.safe_get(self.spec, 'servers', [])
            if isinstance(root_servers, list):
                servers.extend(root_servers)
            
            # Override with path level servers if they exist
            if path_item and isinstance(path_item, dict):
                path_servers = self.safe_get(path_item, 'servers', [])
                if isinstance(path_servers, list) and path_servers:
                    servers = path_servers  # Replace root servers
            
            # Override with operation level servers if they exist (highest priority)
            if operation and isinstance(operation, dict):
                operation_servers = self.safe_get(operation, 'servers', [])
                if isinstance(operation_servers, list) and operation_servers:
                    servers = operation_servers  # Replace previous servers
            
            # Process and format server information
            formatted_servers = []
            for i, server in enumerate(servers):
                try:
                    dereferenced_server = self.dereference_object(server)
                    if isinstance(dereferenced_server, dict):
                        server_info = {
                            'url': self.safe_get(dereferenced_server, 'url', ''),
                            'description': self.safe_get(dereferenced_server, 'description', ''),
                            'variables': self.safe_get(dereferenced_server, 'variables', {})
                        }
                        formatted_servers.append(server_info)
                except Exception as e:
                    print(f"Warning: Error processing server {i}: {e}")
                    # Add a fallback server
                    formatted_servers.append({
                        'url': '',
                        'description': f'Error processing server: {e}',
                        'variables': {}
                    })
            
            # If no servers found, provide a default empty server
            if not formatted_servers:
                formatted_servers = [{
                    'url': '',
                    'description': 'No server information available',
                    'variables': {}
                }]
            
            # If base_url is provided, override all server URLs
            if self.base_url:
                for server in formatted_servers:
                    server['url'] = self.base_url
                    server['description'] = f"Base URL override: {self.base_url}"
            
            return formatted_servers
            
        except Exception as e:
            print(f"Warning: Error extracting servers: {e}")
            return [{
                'url': self.base_url if self.base_url else '',
                'description': f'Error extracting servers: {e}',
                'variables': {}
            }]
    
    def extract_paths(self) -> List[tuple]:
        """
        Extract all paths with their details with comprehensive error handling
        Returns a list of tuples (path_info, tags)
        """
        try:
            paths = self.safe_get(self.spec, 'paths', {})
            extracted_paths = []
            
            if not isinstance(paths, dict):
                print("Warning: 'paths' is not a dictionary")
                return []
            
            for endpoint, path_item in paths.items():
                try:
                    print(f"Processing endpoint: {endpoint}")
                    
                    # Dereference the entire path item first
                    dereferenced_path_item = self.dereference_object(path_item)
                    
                    if not isinstance(dereferenced_path_item, dict):
                        print(f"Warning: Path item for {endpoint} is not a dictionary")
                        continue
                    
                    # Extract common parameters at path level
                    path_parameters = self.safe_get(dereferenced_path_item, 'parameters', [])
                    
                    # Process each HTTP method
                    for method in ['get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace']:
                        if method in dereferenced_path_item:
                            try:
                                operation = dereferenced_path_item[method]
                                
                                if not isinstance(operation, dict):
                                    print(f"Warning: Operation {method} for {endpoint} is not a dictionary")
                                    continue
                                
                                # Combine path-level and operation-level parameters
                                operation_parameters = self.safe_get(operation, 'parameters', [])
                                all_parameters = []
                                
                                if isinstance(path_parameters, list):
                                    all_parameters.extend(path_parameters)
                                if isinstance(operation_parameters, list):
                                    all_parameters.extend(operation_parameters)
                                
                                # Extract all required information
                                operation_id = self.safe_get(operation, 'operationId', f"{method}_{endpoint.replace('/', '_').replace('{', '').replace('}', '')}")
                                
                                # Only convert to camelCase if it's not already in a proper format
                                if re.search(r'[^a-zA-Z0-9]', operation_id):
                                    operation_id = to_camel_case(operation_id)
                                
                                description = self.safe_get(operation, 'description', self.safe_get(operation, 'summary', ''))
                                
                                # Extract servers and create full URL
                                servers = self.extract_servers(dereferenced_path_item, operation)
                                base_url = ''
                                if servers and len(servers) > 0:
                                    base_url = servers[0].get('url', '').rstrip('/')
                                
                                # Create full URL by combining base URL with endpoint
                                full_url = base_url + endpoint if base_url else endpoint
                                
                                # Convert to JSON schema format
                                request_body = self.safe_get(operation, 'requestBody', {})
                                responses = self.safe_get(operation, 'responses', {})
                                
                                path_info = {
                                    'name': operation_id,
                                    'description': description,
                                    'method': method.upper(),
                                    'endpoint': full_url,
                                    'headers': [
                                        {
                                            "name": "Authorization",
                                            "required": True
                                        }
                                    ],
                                    'parameters': self.convert_parameters_to_json_schema(all_parameters),
                                    'body': self.convert_request_body_to_json_schema(request_body),
                                    'response': self.convert_response_to_json_schema(responses)
                                }
                                
                                # Store tags separately for organizing output
                                path_tags = self.safe_get(operation, 'tags', [])
                                if not path_tags:
                                    path_tags = ['untagged']  # Default tag if none provided
                                
                                extracted_paths.append((path_info, path_tags))
                                print(f"Successfully processed: {method.upper()} {endpoint}")
                                
                            except Exception as e:
                                print(f"Warning: Error processing {method} {endpoint}: {e}")
                                continue
                
                except Exception as e:
                    print(f"Warning: Error processing endpoint {endpoint}: {e}")
                    continue
            
            return extracted_paths
            
        except Exception as e:
            print(f"Error in extract_paths: {e}")
            return []

def extract_openapi_paths(file_path: str, base_url: str = None) -> List[tuple]:
    """
    Main function to extract paths from OpenAPI spec file with enhanced error handling
    Returns a list of tuples (path_info, tags)
    """
    try:
        print(f"Reading file: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as file:
            openapi_spec = json.load(file)
        
        print("File loaded successfully")
        print(f"OpenAPI version: {openapi_spec.get('openapi', 'unknown')}")
        
        extractor = OpenAPIPathExtractor(openapi_spec, base_url)
        paths = extractor.extract_paths()
        
        print(f"Extraction completed. Found {len(paths)} paths.")
        return paths
    
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return []
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in file '{file_path}': {e}")
        return []
    except Exception as e:
        print(f"Error processing OpenAPI spec: {e}")
        import traceback
        traceback.print_exc()
        return []

def save_extracted_paths(paths: List[tuple], api_name: str = "api", api_version: str = "1", output_dir: str = "output"):
    """
    Save extracted paths organized by tags
    Creates a folder structure with:
    - output/
      - page.json (main index with all tags)
      - tag1/
        - page.json (contains all endpoints with tag1)
      - tag2/
        - page.json (contains all endpoints with tag2)
    """
    try:
        # Delete existing output directory if it exists
        if os.path.exists(output_dir):
            print(f"Removing existing directory: {output_dir}")
            shutil.rmtree(output_dir)
        
        # Create output directory
        create_directory(output_dir)
        
        # Organize paths by tag
        tag_paths = {}
        all_tags = set()
        
        for path_info, tags in paths:
            # Use first tag as primary for folder organization
            primary_tag = tags[0]
            all_tags.add(primary_tag)
            
            if primary_tag not in tag_paths:
                tag_paths[primary_tag] = []
            
            tag_paths[primary_tag].append(path_info)
        
        # Create tag folders and page.json files
        for tag, tag_paths_list in tag_paths.items():
            # Create folder for tag
            tag_dir = os.path.join(output_dir, tag)
            create_directory(tag_dir)
            
            # Create page.json for tag
            tag_page = {
                "name": tag,
                "tools": tag_paths_list
            }
            
            with open(os.path.join(tag_dir, "page.json"), 'w', encoding='utf-8') as file:
                json.dump(tag_page, file, indent=2, ensure_ascii=False)
            
            print(f"Saved {len(tag_paths_list)} paths for tag '{tag}'")
        
        # Create main page.json with all tags
        main_page = {
            "name": api_name,
            "version": api_version,
            "tools": [{"name": tag} for tag in sorted(all_tags)]
        }
        
        with open(os.path.join(output_dir, "page.json"), 'w', encoding='utf-8') as file:
            json.dump(main_page, file, indent=2, ensure_ascii=False)
        
        print(f"\nExtracted paths organized by {len(all_tags)} tags and saved to {output_dir}/")
        
    except Exception as e:
        print(f"Error saving extracted paths: {e}")
        import traceback
        traceback.print_exc()

def process_openapi(url: str, api_name: str = "api", output_dir: str = "output", base_url: str = None, force_convert=False):
    """
    Main function to process a Swagger/OpenAPI URL
    1. Convert to OpenAPI (or use cached version)
    2. Extract paths
    3. Save extracted paths organized by tags
    """
    # Step 1: Convert Swagger to OpenAPI (or use cached version)
    openapi_file = convert_swagger_to_openapi(url, force_convert)
    
    if not openapi_file:
        print("Failed to process OpenAPI spec. Aborting.")
        return False
    
    # Step 2: Extract paths
    extracted_paths = extract_openapi_paths(openapi_file, base_url)
    
    if not extracted_paths:
        print("No paths extracted. Check the error messages above.")
        return False
    
    # Step 3: Save extracted paths organized by tags
    save_extracted_paths(extracted_paths, api_name, "1", output_dir)
    
    print(f"\nSuccessfully processed API: {api_name}")
    
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process OpenAPI/Swagger specification')
    parser.add_argument('url', help='URL or path to the Swagger/OpenAPI specification')
    parser.add_argument('api_name', help='Name of the API')
    parser.add_argument('--output', default='output', help='Output directory (default: output)')
    parser.add_argument('--base', help='Base URL override for all endpoints')
    parser.add_argument('--force', action='store_true', help='Force conversion even if cached version exists')
    
    args = parser.parse_args()
    
    process_openapi(args.url, args.api_name, args.output, args.base, args.force) 