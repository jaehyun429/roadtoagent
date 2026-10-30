from google import genai
from dotenv import load_dotenv
load_dotenv()
import json

read_file_function = {
    "type": "function",
    "name": "read_file",
    "description": "파일 내용을 읽어온다.",
    "parameters" : {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read"
            }
        },
        "required": ["path"]
    }
}

write_file_function = {
    "type": "function",
    "name": "write_file",
    "description": "파일에 내용을 작성한다.",
    "parameters" : {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write"
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file"
            }
        },
        "required": ["path", "content"]
    }
}

run_python_function = {
    "type": "function",
    "name": "run_python",
    "description": "실행 결과의 stdout과 stderr을 문자열로 반환한다, 10초 타임아웃",
    "parameters" : {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute"
            }
        },
        "required": ["code"]
    }
}

def read_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as file:
            
            content = file.read()
            return content
    except Exception as e:
        return f"ERROR: {str(e)}"
        
        
def write_file(path: str, content: str) -> str:
    try:
        with open(path, 'w', encoding='utf-8') as file:
            s = file.write(content)
            print(f"saved:{path}")
            return f"saved:{path}"
    except Exception as e:
        return f"ERROR: {str(e)}"  

def run_python(code: str) -> str:
    import subprocess
    try:
        result = subprocess.run(['python', '-c', code], capture_output=True, text=True, timeout=10)
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "ERROR: Execution timed out."
    except Exception as e:
        return f"ERROR: {str(e)}"
    

client = genai.Client()
task = "data/sample.csv를 읽어서 숫자 컬럼별 평균을 계산하고 결과를 result.md에 마크다운 표로 저장해"

TOOLS = [read_file_function, write_file_function, run_python_function]


interaction = client.interactions.create(
    model="gemini-3.6-flash",
    input=f"{task}",
    tools=TOOLS
)

TOOL_MAP = {
    "read_file": read_file,
    "write_file": write_file,
    "run_python": run_python
}
tokens = 0
step = 0
while step < 15:
    step += 1
    calls = [s for s in interaction.steps if s.type == "function_call"]
    if not calls:
        print(interaction.output_text)
        print(f"총 스텝: {step}")
        tokens += interaction.usage.total_tokens
        print(f"총 토큰 사용량: {tokens}")
        break
    results = []
    for c in calls:
        if c.name in TOOL_MAP:
            result = TOOL_MAP[c.name](**c.arguments)
        else:
            result = f"ERROR: Unknown tool {c.name}"
        results.append({
            "type": "function_result",
            "name": c.name,
            "call_id": c.id,
            "result": [{"type": "text", "text": json.dumps(result)}]
        })
        print(f"STEP {step} / {c.name} {c.id} / {result[:200]}")  # Print first 200 characters of the result
    tokens += interaction.usage.total_tokens 
    interaction = client.interactions.create(
        model="gemini-3.6-flash",
        previous_interaction_id=interaction.id,
        tools=TOOLS,
        input=results
    )
else:
    print("STEP_LIMIT")
    print(f"총 스텝: {step}")
    print(f"총 토큰 사용량: {tokens}")

