from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Annotated, Optional, List

import os
import boto3
import telegram
import io

from PyPDF2 import PdfMerger
from PIL import Image 
from dotenv import load_dotenv
from botocore.exceptions import ClientError
from telegram.request import HTTPXRequest


load_dotenv()

ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("BUCKET_NAME")  
R2_ENDPOINT_URL = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID", "")


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

def merge_pdfs(all_pdf_buffer):
    merger = PdfMerger()

    for pdf_buffer in all_pdf_buffer:
        try:
            pdf_buffer.seek(0)
            buffer_content = pdf_buffer.read()
            # pdf_reader = PdfReader(pdf_buffer)
            merger.append(io.BytesIO(buffer_content))
        except Exception as e:
            print(f"Error appending pdfs: {e}")
            

    output_pdf_buffer = io.BytesIO() 
    try:
        merger.write(output_pdf_buffer)
        output_pdf_buffer.seek(0)
        print("Successfully merged all PDFs into a single buffer.")
        return output_pdf_buffer
    except Exception as e:
        print(f"Error while merging: {e}")
        
    finally:
        merger.close()

async def send_merged_pdf_bot(pdf_buffer, filename, caption=""):
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=120,
        write_timeout=120,
        pool_timeout=30,
    )
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN, request=request)

    try:
        async with bot:
            pdf_buffer.seek(0)
            await bot.send_document(
                chat_id=TELEGRAM_GROUP_CHAT_ID,
                document=pdf_buffer,
                filename=f"{filename}.pdf",
                caption=caption
            )
            print("PDF sent successfully.")
    except Exception as e:
        print(f"An error occurred: {e}")


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
    gradereports: Annotated[List[UploadFile], File()],
    degree: Annotated[Optional[UploadFile], File()]
):
    file_fields = {
        "id_card": id_card,
        "entrance_exam": entrance,
        "transcript": transcript,
        "grade_report": gradereports,
        "degree": degree
    }

    sanitized_fullname = fullname.replace(' ', '_').lower()
    all_pdf_buffer = []
    uploaded_file_info = []
    try:
        for file_name, file in file_fields.items():
            if file is None:
                continue

            if not isinstance(file, List) and not file.filename:
                continue
            
            final_file_name = f"{sanitized_fullname}_{file_name}"

            if file_name == "grade_report":
                grade_report_pdf_list = []
                for g_report in gradereports:
                    image_content = await g_report.read()
                    image_bytes_io = io.BytesIO(image_content)
                    pdf_buffer = image_to_pdf(image_bytes_io)
                    pdf_buffer.seek(0) # type: ignore
                    grade_report_pdf_list.append(pdf_buffer)
                merged_grade_report = merge_pdfs(grade_report_pdf_list)

                merged_grade_report.seek(0) # type: ignore
                copy_merged_pdfs = io.BytesIO(merged_grade_report.read()) # type: ignore
                merged_grade_report.seek(0) # type: ignore

                all_pdf_buffer.append(copy_merged_pdfs)

                upload_pdf_to_r2(final_file_name, merged_grade_report, BUCKET_NAME)
                uploaded_file_info.append({
                        "field": "grade_report",
                        "saved_as": f"{final_file_name}.pdf"
                    })
                continue
                

            image_content = await file.read()
            image_bytes_io = io.BytesIO(image_content)

            pdf_buffer = image_to_pdf(image_bytes_io)
            
            pdf_buffer.seek(0) # type: ignore
            copy_pdf_buffer = io.BytesIO(pdf_buffer.read()) # type: ignore
            pdf_buffer.seek(0) # type: ignore

            all_pdf_buffer.append(copy_pdf_buffer)

            upload_pdf_to_r2(final_file_name, pdf_buffer, BUCKET_NAME)
            uploaded_file_info.append({
                "field": file_name,
                "saved_as": f"{final_file_name}.pdf"
            })

        
        merged_pdfs = merge_pdfs(all_pdf_buffer)
        new_merged_pdf_file_name = f"{sanitized_fullname}_doc_report"

        merged_pdfs.seek(0) # type: ignore
        copy_merged_pdfs = io.BytesIO(merged_pdfs.read()) # type: ignore
        merged_pdfs.seek(0) # type: ignore

        upload_pdf_to_r2(new_merged_pdf_file_name, merged_pdfs, BUCKET_NAME)
        uploaded_file_info.append({
                "field": "document_report",
                "saved_as": f"{new_merged_pdf_file_name}.pdf"
            })
        
        # send to telegram
        await send_merged_pdf_bot(copy_merged_pdfs, new_merged_pdf_file_name, fullname)

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
    