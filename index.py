from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Annotated

import os
import boto3
import io

from PIL import Image 
from dotenv import load_dotenv
from pathlib import Path
from botocore.exceptions import ClientError


load_dotenv()

ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME")  
R2_ENDPOINT_URL = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"


def image_to_pdf(image_obj):
    pdf_buffer = io.BytesIO()
    try:
        image = Image.open(image_obj)

        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
            
        image.save(pdf_buffer, "PDF", resolution=100.0)
        pdf_buffer.seek(0)
        print("Successfully converted to PDF in memory.")
        return pdf_buffer

    except Exception as e:
        print(f"An error occured during image processing: {e}")


def upload_pdf_to_r2(file_name, pdf_buffer, bucket_name):
    r2_object_name = f"{file_name}.pdf"
    try:
        s3 = boto3.client(
            service_name='s3',
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=ACCESS_KEY_ID,
            aws_secret_access_key=SECRET_ACCESS_KEY,
            region_name="auto"
        )
        print(f"Connecting to R2 at: {R2_ENDPOINT_URL}...")

        s3.upload_fileobj(
            pdf_buffer,
            bucket_name,
            r2_object_name,
            ExtraArgs={'ContentType': 'application/pdf'}
        )
       
        print(f"Successfully uploaded the PDF file to R2 as '{r2_object_name}' in bucket '{bucket_name}'.")
    except ClientError as e:
        print(f"An error occured during upload: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

# Endpoint
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.post("/upload-ducuments")
async def upload_documents(
    fullname: Annotated[str, Form()],
    id_card: Annotated[UploadFile, File()],
    entrance: Annotated[UploadFile, File()],
    transcript: Annotated[UploadFile, File()],
    gradereport: Annotated[UploadFile, File()],
    degree: Annotated[UploadFile, File()]
):
    file_fields = {
        "id_card": id_card,
        "entrance_exam": entrance,
        "transcript": transcript,
        "grade_report": gradereport,
        "degree": degree
    }
    uploaded_file_info = []
    try:
        for file_name, file in file_fields.items():
            if file.filename is None or not file.filename:
                raise ValueError(f"No filename provided for {file_name}")
            
            sanitized_fullname = fullname.replace(' ', '_').lower()
            # file_extension = os.path.splitext(file.filename)[1]
            final_file_name = f"{sanitized_fullname}_{file_name}"

            image_content = await file.read()
            image_bytes_io = io.BytesIO(image_content)

            pdf_buffer = image_to_pdf(image_bytes_io)

            upload_pdf_to_r2(final_file_name, pdf_buffer, BUCKET_NAME)
            uploaded_file_info.append({
                "field": file_name,
                "saved_as": f"{final_file_name}.pdf"
            })
        print(uploaded_file_info)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Image processed and saved successfully.",
                "fullname": fullname,
                "uploaded": uploaded_file_info
            }
        )
    except Exception as e:
        print(f"An error occurred: {e}")
        raise HTTPException(status_code=500, detail=f"File upload failed due to a server error: {e}")
    