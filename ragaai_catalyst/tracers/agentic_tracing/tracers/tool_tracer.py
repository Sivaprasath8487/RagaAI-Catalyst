import os
import uuid
from datetime import datetime
import psutil
import functools
from typing import Optional, Any, Dict, List
from ..utils.unique_decorator import generate_unique_hash_simple
import contextvars
import asyncio
from ..utils.file_name_tracker import TrackName
from ..utils.span_attributes import SpanAttributes
import logging
import wrapt
import time

logger = logging.getLogger(__name__)
logging_level = (
    logger.setLevel(logging.DEBUG)
    if os.getenv("DEBUG")
    else logger.setLevel(logging.INFO)
)


class ToolTracerMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_tracker = TrackName()
        self.current_tool_name = contextvars.ContextVar("tool_name", default=None)
        self.current_tool_id = contextvars.ContextVar("tool_id", default=None)
        self.component_network_calls = {}
        self.component_user_interaction = {}
        self.gt = None

        # add auto_instrument option
        self.auto_instrument_tool = False
        self.auto_instrument_user_interaction = False
        self.auto_instrument_network = False

    # take care of auto_instrument
    def instrument_tool_calls(self):
        """Enable tool instrumentation"""
        self.auto_instrument_tool = True
        
        # Handle modules that are already imported
        import sys
        
        if "langchain_community.tools.tavily_search" in sys.modules:
            self.patch_tavily_search_methods(sys.modules["langchain_community.tools.tavily_search"])
        if "langgraph.prebuilt" in sys.modules:
            self.patch_langgraph_methods(sys.modules["langgraph.prebuilt"])
        
        # Register hooks for future imports
        wrapt.register_post_import_hook(
            self.patch_tavily_search_methods, "langchain_community.tools.tavily_search"
        )
        wrapt.register_post_import_hook(
            self.patch_langgraph_methods, "langgraph.prebuilt"
        )
        
    def patch_langgraph_methods(self, module):
        """Patch LangGraph ToolNode methods"""
        if hasattr(module, "ToolNode"):
            tool_node_class = getattr(module, "ToolNode")
            
            # Patch the call method which is used by LangGraph
            if hasattr(tool_node_class, "call"):
                self.wrap_method(tool_node_class, "call")
            if hasattr(tool_node_class, "acall"):
                self.wrap_method(tool_node_class, "acall")
                
    def patch_tavily_search_methods(self, module):
        """Patch Tavily Search tool methods"""
        if hasattr(module, "TavilySearchResults"):
            tool_class = getattr(module, "TavilySearchResults")
            
            # Patch the invoke method which is used by LangGraph
            if hasattr(tool_class, "invoke"):
                self.wrap_method(tool_class, "invoke")
            if hasattr(tool_class, "ainvoke"):
                self.wrap_method(tool_class, "ainvoke")
                
            # Also patch the run methods
            if hasattr(tool_class, "_run"):
                self.wrap_method(tool_class, "_run")
            if hasattr(tool_class, "_arun"):
                self.wrap_method(tool_class, "_arun")
            
    def wrap_method(self, obj, method_name):
        """Wrap a method with tracing functionality"""
        if not hasattr(obj, method_name):
            return
            
        original_method = getattr(obj, method_name)
        
        @wrapt.decorator
        def wrapper(wrapped, instance, args, kwargs):
            if asyncio.iscoroutinefunction(wrapped):
                return self.trace_tool_call(wrapped, instance, *args, **kwargs)
            return self.trace_tool_call_sync(wrapped, instance, *args, **kwargs)
            
        wrapped_method = wrapper(original_method)
        setattr(obj, method_name, wrapped_method)
        
    def trace_tool_call(self, original_func, instance, *args, **kwargs):
        """Trace an async tool call"""
        async def wrapper():
            start_time = time.time()
            error = None
            output_data = None
            
            try:
                output_data = await original_func(instance, *args, **kwargs)
            except Exception as e:
                error = str(e)
                raise
            finally:
                end_time = time.time()
                memory_used = psutil.Process(os.getpid()).memory_info().rss
                
                # Get tool name and type based on the instance
                if hasattr(instance, "__class__"):
                    if instance.__class__.__name__ == "ToolNode":
                        # For ToolNode, get the names of all tools
                        tool_names = [t.__class__.__name__ for t in instance.tools]
                        tool_name = f"ToolNode({','.join(tool_names)})"
                        tool_type = "langgraph"
                    else:
                        tool_name = instance.__class__.__name__
                        tool_type = "langchain"
                else:
                    tool_name = original_func.__qualname__.split('.')[0]
                    tool_type = "generic"
                
                hash_id = generate_unique_hash_simple()
                
                self.create_tool_component(
                    component_id=str(uuid.uuid4()),
                    hash_id=hash_id,
                    name=tool_name,
                    tool_type=tool_type,
                    version="1.0.0",
                    memory_used=memory_used,
                    start_time=datetime.fromtimestamp(start_time).isoformat(),
                    input_data={"args": args, "kwargs": kwargs},
                    output_data=output_data,
                    error=error
                )
                
                self.add_component(tool_component)
                
            return output_data
            
        return wrapper()
        
    def trace_tool_call_sync(self, original_func, instance, *args, **kwargs):
        """Trace a sync tool call"""
        start_time = time.time()
        error = None
        output_data = None
        
        try:
            output_data = original_func(instance, *args, **kwargs)
        except Exception as e:
            error = str(e)
            raise
        finally:
            end_time = time.time()
            memory_used = psutil.Process(os.getpid()).memory_info().rss
            
            # Get tool name and type based on the instance
            if hasattr(instance, "__class__"):
                if instance.__class__.__name__ == "ToolNode":
                    # For ToolNode, get the names of all tools
                    tool_names = [t.__class__.__name__ for t in instance.tools]
                    tool_name = f"ToolNode({','.join(tool_names)})"
                    tool_type = "langgraph"
                else:
                    tool_name = instance.__class__.__name__
                    tool_type = "langchain"
            else:
                tool_name = original_func.__qualname__.split('.')[0]
                tool_type = "generic"
            
            hash_id = generate_unique_hash_simple()
            
            self.create_tool_component(
                component_id=str(uuid.uuid4()),
                hash_id=hash_id,
                name=tool_name,
                tool_type=tool_type,
                version="1.0.0",
                memory_used=memory_used,
                start_time=datetime.fromtimestamp(start_time).isoformat(),
                input_data={"args": args, "kwargs": kwargs},
                output_data=output_data,
                error=error
            )
            
            self.add_component(tool_component)
            
        return output_data

    def instrument_user_interaction_calls(self):
        self.auto_instrument_user_interaction = True

    def instrument_network_calls(self):
        self.auto_instrument_network = True

    def trace_tool(
        self,
        name: str,
        tool_type: str = "generic",
        version: str = "1.0.0",
        tags: List[str] = [],
        metadata: Dict[str, Any] = {},
        metrics: List[Dict[str, Any]] = [],
        feedback: Optional[Any] = None,
    ):
        if name not in self.span_attributes_dict:
            self.span_attributes_dict[name] = SpanAttributes(name)
        if tags:
            self.span(name).add_tags(tags)
        if metadata:
            self.span(name).add_metadata(metadata)
        if metrics:
            if isinstance(metrics, dict):
                metrics = [metrics]
            try:
                for metric in metrics:
                    self.span(name).add_metrics(
                        name=metric["name"],
                        score=metric["score"],
                        reasoning=metric.get("reasoning", ""),
                        cost=metric.get("cost", None),
                        latency=metric.get("latency", None),
                        metadata=metric.get("metadata", {}),
                        config=metric.get("config", {}),
                    )
            except ValueError as e:
                    logger.error(f"Validation Error: {e}")
            except Exception as e:
                logger.error(f"Error adding metric: {e}")
            
        if feedback:
            self.span(name).add_feedback(feedback)

        def decorator(func):
            # Add metadata attribute to the function
            metadata = {
                "name": name,
                "tool_type": tool_type,
                "version": version,
                "is_active": self.is_active,
            }

            # Check if the function is async
            is_async = asyncio.iscoroutinefunction(func)

            @self.file_tracker.trace_decorator
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                async_wrapper.metadata = metadata
                self.gt = kwargs.get("gt", None) if kwargs else None
                return await self._trace_tool_execution(
                    func, name, tool_type, version, *args, **kwargs
                )

            @self.file_tracker.trace_decorator
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                sync_wrapper.metadata = metadata
                self.gt = kwargs.get("gt", None) if kwargs else None
                return self._trace_sync_tool_execution(
                    func, name, tool_type, version, *args, **kwargs
                )

            wrapper = async_wrapper if is_async else sync_wrapper
            wrapper.metadata = metadata
            return wrapper

        return decorator

    def _trace_sync_tool_execution(
        self, func, name, tool_type, version, *args, **kwargs
    ):
        """Synchronous version of tool tracing"""
        if not self.is_active:
            return func(*args, **kwargs)

        if not self.auto_instrument_tool:
            return func(*args, **kwargs)

        start_time = datetime.now().astimezone()
        start_memory = psutil.Process().memory_info().rss
        component_id = str(uuid.uuid4())
        hash_id = generate_unique_hash_simple(func)

        # Start tracking network calls for this component
        self.start_component(component_id)

        try:
            # Execute the tool
            result = func(*args, **kwargs)

            # Calculate resource usage
            end_memory = psutil.Process().memory_info().rss
            memory_used = max(0, end_memory - start_memory)

            # End tracking network calls for this component
            self.end_component(component_id)

            # Create tool component
            tool_component = self.create_tool_component(
                component_id=component_id,
                hash_id=hash_id,
                name=name,
                tool_type=tool_type,
                version=version,
                memory_used=memory_used,
                start_time=start_time,
                input_data=self._sanitize_input(args, kwargs),
                output_data=self._sanitize_output(result),
            )

            self.add_component(tool_component)

            return result

        except Exception as e:
            error_component = {
                "code": 500,
                "type": type(e).__name__,
                "message": str(e),
                "details": {},
            }

            # End tracking network calls for this component
            self.end_component(component_id)

            tool_component = self.create_tool_component(
                component_id=component_id,
                hash_id=hash_id,
                name=name,
                tool_type=tool_type,
                version=version,
                memory_used=0,
                start_time=start_time,
                input_data=self._sanitize_input(args, kwargs),
                output_data=None,
                error=error_component,
            )

            self.add_component(tool_component)

            raise

    async def _trace_tool_execution(
        self, func, name, tool_type, version, *args, **kwargs
    ):
        """Asynchronous version of tool tracing"""
        if not self.is_active:
            return await func(*args, **kwargs)

        if not self.auto_instrument_tool:
            return await func(*args, **kwargs)

        start_time = datetime.now().astimezone()
        start_memory = psutil.Process().memory_info().rss
        component_id = str(uuid.uuid4())
        hash_id = generate_unique_hash_simple(func)

        self.start_component(component_id)
        try:
            # Execute the tool
            result = await func(*args, **kwargs)

            # Calculate resource usage
            end_memory = psutil.Process().memory_info().rss
            memory_used = max(0, end_memory - start_memory)
            self.end_component(component_id)

            # Create tool component
            tool_component = self.create_tool_component(
                component_id=component_id,
                hash_id=hash_id,
                name=name,
                tool_type=tool_type,
                version=version,
                start_time=start_time,
                memory_used=memory_used,
                input_data=self._sanitize_input(args, kwargs),
                output_data=self._sanitize_output(result),
            )
            self.add_component(tool_component)

            return result

        except Exception as e:
            error_component = {
                "code": 500,
                "type": type(e).__name__,
                "message": str(e),
                "details": {},
            }

            tool_component = self.create_tool_component(
                component_id=component_id,
                hash_id=hash_id,
                name=name,
                tool_type=tool_type,
                version=version,
                start_time=start_time,
                memory_used=0,
                input_data=self._sanitize_input(args, kwargs),
                output_data=None,
                error=error_component,
            )
            self.add_component(tool_component)

            raise

    def create_tool_component(self, **kwargs):
        """Create a tool component according to the data structure"""
        network_calls = []
        if self.auto_instrument_network:
            network_calls = self.component_network_calls.get(kwargs["component_id"], [])
        interactions = []
        if self.auto_instrument_user_interaction:
            interactions = self.component_user_interaction.get(
                kwargs["component_id"], []
            )

        # Get tags, metrics
        name = kwargs["name"]
        # tags
        tags = []
        if name in self.span_attributes_dict:
            tags = self.span_attributes_dict[name].tags or []

        # metrics
        metrics = []
        if name in self.span_attributes_dict:
            raw_metrics = self.span_attributes_dict[name].metrics or []
            for metric in raw_metrics:
                base_metric_name = metric["name"]
                counter = sum(1 for x in self.visited_metrics if x.startswith(base_metric_name))
                metric_name = f'{base_metric_name}_{counter}' if counter > 0 else base_metric_name
                self.visited_metrics.append(metric_name)
                metric["name"] = metric_name  
                metrics.append(metric)

        start_time = kwargs["start_time"]
        component = {
            "id": kwargs["component_id"],
            "hash_id": kwargs["hash_id"],
            "source_hash_id": None,
            "type": "tool",
            "name": kwargs["name"],
            "start_time": start_time.isoformat(),
            "end_time": datetime.now().astimezone().isoformat(),
            "error": kwargs.get("error"),
            "parent_id": self.current_agent_id.get(),
            "info": {
                "tool_type": kwargs["tool_type"],
                "version": kwargs["version"],
                "memory_used": kwargs["memory_used"],
                "tags": tags,
            },
            "data": {
                "input": kwargs["input_data"],
                "output": kwargs["output_data"],
                "memory_used": kwargs["memory_used"],
            },
            "metrics": metrics,
            "network_calls": network_calls,
            "interactions": interactions,
        }

        if self.gt:
            component["data"]["gt"] = self.gt

        # Reset the SpanAttributes context variable
        self.span_attributes_dict[kwargs["name"]] = SpanAttributes(kwargs["name"])

        return component

    def start_component(self, component_id):
        self.component_network_calls[component_id] = []

    def end_component(self, component_id):
        pass

    def _sanitize_input(self, args: tuple, kwargs: dict) -> Dict:
        """Sanitize and format input data"""
        return {
            "args": [
                (
                    str(arg)
                    if not isinstance(arg, (int, float, bool, str, list, dict))
                    else arg
                )
                for arg in args
            ],
            "kwargs": {
                k: (
                    str(v)
                    if not isinstance(v, (int, float, bool, str, list, dict))
                    else v
                )
                for k, v in kwargs.items()
            },
        }

    def _sanitize_output(self, output: Any) -> Any:
        """Sanitize and format output data"""
        if isinstance(output, (int, float, bool, str, list, dict)):
            return output
        return str(output)
