import os
import json
import tempfile
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
import logging
from datetime import datetime
from dataclasses import asdict
from ragaai_catalyst.tracers.utils.trace_json_converter import convert_json_format
from ragaai_catalyst.tracers.agentic_tracing.tracers.base import TracerJSONEncoder
from ragaai_catalyst.tracers.agentic_tracing.utils.system_monitor import SystemMonitor
from ragaai_catalyst.tracers.agentic_tracing.upload.trace_uploader import submit_upload_task
from ragaai_catalyst.tracers.agentic_tracing.utils.zip_list_of_unique_files import zip_list_of_unique_files


logger = logging.getLogger("RagaAICatalyst")
logging_level = (
    logger.setLevel(logging.DEBUG) if os.getenv("DEBUG") == "1" else logging.INFO
)


class RAGATraceExporter(SpanExporter):
    def __init__(self, files_to_zip, project_name, project_id, dataset_name, user_details, base_url):
        self.trace_spans = dict()
        self.tmp_dir = tempfile.gettempdir()
        self.files_to_zip = files_to_zip
        self.project_name = project_name
        self.project_id = project_id
        self.dataset_name = dataset_name
        self.user_details = user_details
        self.base_url = base_url
        self.system_monitor = SystemMonitor(dataset_name)

    def export(self, spans):
        for span in spans:
            span_json = json.loads(span.to_json())
            trace_id = span_json.get("context").get("trace_id")

            if trace_id not in self.trace_spans:
                self.trace_spans[trace_id] = list()

            self.trace_spans[trace_id].append(span_json)

            if span_json["parent_id"] is None:
                trace = self.trace_spans[trace_id]
                self.process_complete_trace(trace, trace_id)
                del self.trace_spans[trace_id]

        return SpanExportResult.SUCCESS

    def shutdown(self):
        # Process any remaining traces during shutdown
        for trace_id, spans in self.trace_spans.items():
            self.process_complete_trace(spans, trace_id)
        self.trace_spans.clear()

    def process_complete_trace(self, spans, trace_id):
        # # Save trace to temp file with trace_id.jsonl name
        # trace_file_path = os.path.join(self.tmp_dir, f"{trace_id}.jsonl")
        
        # with open(trace_file_path, "w") as file:
        #     for record in spans:
        #         file.write(json.dumps(record) + "\n")
        
        # Convert the trace to ragaai trace format
        ragaai_trace = None
        try:
            ragaai_trace_details = self.prepare_trace(spans, trace_id)
        except Exception as e:
            print(f"Error converting trace {trace_id}: {e}")
        
        # Upload the trace if upload_trace function is provided
        try:
            # Upload either the cleaned trace or the original file path
            self.upload_trace(ragaai_trace_details, trace_id)
        except Exception as e:
            # Handle or log the error
            print(f"Error uploading trace {trace_id}: {e}")

    def prepare_trace(self, spans, trace_id):
        try:
            ragaai_trace = convert_json_format(spans)            
            ragaai_trace["workflow"] = []

            # Add source code hash
            hash_id, zip_path = zip_list_of_unique_files(
                self.files_to_zip, output_dir=self.tmp_dir
            )
            ragaai_trace["metadata"]["system_info"] = asdict(self.system_monitor.get_system_info())
            ragaai_trace["metadata"]["resources"] = asdict(self.system_monitor.get_resources())
            ragaai_trace["metadata"]["system_info"]["source_code"] = hash_id
            
            # Save the trace_json 
            trace_file_path = os.path.join(self.tmp_dir, f"{trace_id}.json")
            with open(trace_file_path, "w") as file:
                json.dump(ragaai_trace, file, cls=TracerJSONEncoder, indent=2)

            return {
                'trace_file_path': trace_file_path,
                'code_zip_path': zip_path,
                'hash_id': hash_id
            }
        except Exception as e:
            logger.error(f"Error converting trace {trace_id}: {str(e)}")
            return None

    def upload_trace(self, ragaai_trace_details, trace_id):
        filepath = ragaai_trace_details['trace_file_path']
        hash_id = ragaai_trace_details['hash_id']
        zip_path = ragaai_trace_details['code_zip_path']

        self.upload_task_id = submit_upload_task(
                filepath=filepath,
                hash_id=hash_id,
                zip_path=zip_path,
                project_name=self.project_name,
                project_id=self.project_id,
                dataset_name=self.dataset_name,
                user_details=self.user_details,
                base_url=self.base_url
            )

        logger.info(f"Submitted upload task with ID: {self.upload_task_id}")