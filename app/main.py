import httpx
import json
import asyncio
import logging

from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, conint
from minio import Minio
from clickhouse_driver import Client
from io import BytesIO
from datetime import datetime


# Setting up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()  # Convert datetime to ISO 8601 format
        return super().default(obj)


app = FastAPI()

minio_client = Minio(
    "minio:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    secure=False
)
ch_client = Client(host='clickhouse', port=9000, user='default', password='', database='default', secure=False,
                   connect_timeout=10,  send_receive_timeout=30)


# Bucket name
bucket_name = "ny-yellow-taxis"
# Endpoint ny taxis
endpoint = "https://d37ci6vzurychx.cloudfront.net/trip-data"

if not minio_client.bucket_exists(bucket_name):
    minio_client.make_bucket(bucket_name)


def calculate_trips_over_percentile(years: List[int], months: List[int], counter: bool):
    years_glob = "{" + ",".join(map(str, years)) + "}" if len(years) > 1 else years[0]
    months_glob = "{" + ",".join(f"{month:02d}" for month in months) + "}" if len(months) > 1 else f"{months[0]:02d}"
    s3_path = f"http://minio:9000/ny-yellow-taxis/bronze/yellow_tripdata_{years_glob}-{months_glob}.parquet"
    columns = "count(*)" if counter else "*"

    query = f"""
            SELECT {columns}
            FROM s3('{s3_path}', 'minioadmin', 'minioadmin', 'Parquet')
            WHERE trip_distance > (
                SELECT quantile(0.9)(trip_distance) 
                FROM s3('{s3_path}', 'minioadmin', 'minioadmin', 'Parquet')
            )
            """
    result = ch_client.execute(query)
    return result


@app.get("/download_parquet/{year}/{month}")
async def download_parquet(year: int, month: int):
    """
    Downloads Parquet files based on the year and month, then uploads it to MinIO.
    """
    month_str = f"{month:02d}"

    file_url = f"{endpoint}/yellow_tripdata_{year}-{month_str}.parquet"

    async with httpx.AsyncClient() as client:
        response = await client.get(file_url)

        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="File download failed.")

        file_data = response.content

    file_name = f"bronze/yellow_tripdata_{year}-{month_str}.parquet"

    try:
        minio_client.put_object(
            bucket_name,
            file_name,
            BytesIO(file_data),
            len(file_data)
        )
        return {"message": f"File yellow_tripdata_{year}-{month_str}.parquet uploaded successfully to MinIO."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading file to MinIO: {str(e)}")


class QueryParams(BaseModel):
    years: List[int] = Query(default=list(range(2009, 2025)))
    months: Optional[List[conint(ge=1, le=12)]] = Query(default=list(range(1, 13)))
    counter: bool = Query(default=True)


@app.post("/trips_over_percentile/")
async def trips_over_percentile(params: QueryParams = Depends()):
    """
       Calculates all the trips over 0.9 percentile in distance traveled
    """

    try:
        years = params.years
        months = params.months

        result = calculate_trips_over_percentile(years=years, months=months, counter=params.counter)

        file_path = "/tmp/query_result.json"
        with open(file_path, "w") as f:
            json.dump(result, f, cls=DateTimeEncoder)

        return FileResponse(file_path, media_type="application/json", filename="query_result.json")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def download_chunk(url, start, end):
    """Download a specific chunk of a file."""
    headers = {"Range": f"bytes={start}-{end}"}
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(url, headers=headers)
        if response.status_code not in [200, 206]:
            raise HTTPException(status_code=400, detail=f"Chunk download failed: {start}-{end}")
        content = response.content
        logger.info(f"Downloaded chunk {start}-{end}")
        return content


async def download_file_chunked(url, chunk_size=10_000_000):
    """Download a file in chunks and return the complete file content."""
    logger.info(f"Downloading {url} to {chunk_size} bytes")
    async with httpx.AsyncClient(http2=True) as client:
        # Get file size from headers
        response = await client.head(url)
        logger.info(f"download_file_chunked response {response}")
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to retrieve file size.")
        file_size = int(response.headers['Content-Length'])

    logger.info(f"File size: {file_size} bytes.")

    tasks = [
        download_chunk(url, start, min(start + chunk_size - 1, file_size - 1))
        for start in range(0, file_size, chunk_size)
    ]
    chunks = await asyncio.gather(*tasks)

    return b"".join(chunks)


async def download_and_upload(year: int, month: int, chunk_size=10_000_000):
    """Downloads a Parquet file in chunks and uploads it to MinIO."""
    month_str = f"{month:02d}"
    file_url = f"{endpoint}/yellow_tripdata_{year}-{month_str}.parquet"

    logger.info(f"Downloading {file_url}")

    try:
        file_data = await download_file_chunked(file_url, chunk_size)
    except HTTPException as e:
        logger.info(f"Error downloading {file_url}: {str(e)}")
        return {"year": year, "month": month, "status": "error", "detail": str(e)}

    file_name = f"bronze/yellow_tripdata_{year}-{month_str}.parquet"
    try:
        minio_client.put_object(
            bucket_name,
            file_name,
            BytesIO(file_data),
            len(file_data)
        )
        return {"year": year, "month": month, "status": "success"}
    except Exception as e:
        logger.info(f"Error uploading file to MinIO: {str(e)}")
        return {"year": year, "month": month, "status": "error", "detail": str(e)}


class RangeParams(BaseModel):
    years: List[int] = Query(default=[2024])
    months: Optional[List[conint(ge=1, le=12)]] = Query(default=list(range(1, 10)))
    chunk_size: int = Query(default=10_000_000)
    calculate_trips: bool = Query(default=True)


@app.post("/download_multiple_parquet_and_get_trips/")
async def download_multiple_parquet_and_get_trips(params: RangeParams = Depends()):
    """
    Downloads Parquet files based on the provided years and months in chunks, then uploads them to MinIO and
    calculate trips over 0.9 percentile.
    """
    years = params.years
    months = params.months
    chunk_size = params.chunk_size

    tasks = [
        download_and_upload(year, month, chunk_size)
        for year in years
        for month in months
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    logger.info(f"Results download_multiple_parquet {len(results)} parquet files downloaded. {results}")

    success = [res for res in results if isinstance(res, dict) and res["status"] == "success"]
    errors = [res for res in results if isinstance(res, dict) and res["status"] == "error"]

    result = None
    if params.calculate_trips:
        result = calculate_trips_over_percentile(years=years, months=months, counter=True)

    return {
        "success": success,
        "errors": errors,
        "summary": {
            "total_attempted": len(tasks),
            "total_successful": len(success),
            "total_failed": len(errors),
            "total_trips_over_percentile": result
        }
    }
