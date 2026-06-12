from google import genai

client = genai.Client(api_key="AQ.Ab8RN6KBfEi5OGrPdmTbp0t3QYwk8MUfP5PhFH22AiY-3n6hXw")

for model in client.models.list():
    print(model.name)