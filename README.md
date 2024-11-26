# NYC “Yellow Taxi” Trips Data

## Goal

Return all the trips over 0.9 percentile in distance traveled

## Analysis Challenge 

### Server Analysis

```
$ curl -I -X HEAD https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet
```

HTTP/2 200 
content-type: binary/octet-stream
content-length: 49961641
last-modified: Thu, 21 Mar 2024 15:35:44 GMT
x-amz-server-side-encryption: AES256
accept-ranges: bytes
server: AmazonS3
date: Mon, 25 Nov 2024 12:46:09 GMT
etag: "d6ebae95a45f284994e283e2df97c06f-3"
x-cache: Hit from cloudfront
via: 1.1 7270d267f6bffc8aa59d396dd86d60a8.cloudfront.net (CloudFront)
x-amz-cf-pop: MAD53-P3
x-amz-cf-id: 0Ep7Wns-topETwfGVULkQkLh86IGEUoiwiq9y6FjizvFXVicLwY2yg==
age: 32662


By analyzing the output we can gather interesting information to resolve the client request:

- The server is using the HTTP/2 protocol.
- The size of the file in bytes, which is 49961641 bytes (around 50 MB).
- Indicates the server supports range requests, allowing clients to download parts of the file
- The server hosting the file is Amazon S3.


### Data Analysis

By accessing the clickhouse client I can use DESCRIBE TABLE statement to check the schema of the parquet files.

```
DESCRIBE TABLE s3('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet')
```

Query id: ce72cf18-c3b6-46d1-a316-6fc65b2b4d28

| **Column Name**         | **Data Type**                | **Default Type** | **Default Expression** | **Comment** | **Codec Expression** | **TTL Expression** |
|-------------------------|------------------------------|------------------|------------------------|-------------|----------------------|---------------------|
| VendorID                | Nullable(Int32)              |                  |                        |             |                      |                     |
| tpep_pickup_datetime    | Nullable(DateTime64(6))      |                  |                        |             |                      |                     |
| tpep_dropoff_datetime   | Nullable(DateTime64(6))      |                  |                        |             |                      |                     |
| passenger_count         | Nullable(Int64)              |                  |                        |             |                      |                     |
| trip_distance           | Nullable(Float64)            |                  |                        |             |                      |                     |
| RatecodeID              | Nullable(Int64)              |                  |                        |             |                      |                     |
| store_and_fwd_flag      | Nullable(String)             |                  |                        |             |                      |                     |
| PULocationID            | Nullable(Int32)              |                  |                        |             |                      |                     |
| DOLocationID            | Nullable(Int32)              |                  |                        |             |                      |                     |
| payment_type            | Nullable(Int64)              |                  |                        |             |                      |                     |
| fare_amount             | Nullable(Float64)            |                  |                        |             |                      |                     |
| extra                   | Nullable(Float64)            |                  |                        |             |                      |                     |
| mta_tax                 | Nullable(Float64)            |                  |                        |             |                      |                     |
| tip_amount              | Nullable(Float64)            |                  |                        |             |                      |                     |
| tolls_amount            | Nullable(Float64)            |                  |                        |             |                      |                     |
| improvement_surcharge   | Nullable(Float64)            |                  |                        |             |                      |                     |
| total_amount            | Nullable(Float64)            |                  |                        |             |                      |                     |
| congestion_surcharge    | Nullable(Float64)            |                  |                        |             |                      |                     |
| Airport_fee             | Nullable(Float64)            |                  |                        |             |                      |                     |


```
select trip_distance from s3('http://minio:9000/ny-yellow-taxis/bronze/yellow_tripdata_2024-09.parquet', 'minioadmin', 'minioadmin', 'Parquet');
```

```
select trip_distance from s3('http://minio:9000/ny-yellow-taxis/bronze/yellow_tripdata_*.parquet', 'minioadmin', 'minioadmin', 'Parquet');
```


#### Discoveries

- The column names are not common across parquet files. (eg. I could see Trip_Distance and trip_distance in different parquet files). Column names in databases 
and file formats like Parquet are case-sensitive, so a mismatch causes the query to fail.
  - For dealing with this scenario we could:
    - 1. Preprocess the data to unify column names. This would add some extra time because it would suppose to include a preprocess step.
    - 2. Use DuckDB (in-process SQL OLAP database) that supports UNION BY NAME, which joins columns by name instead of by position
- There are rows in those parquet files where the trip distance is null. 


## How to run the project  

### Start and run the project

```
$ docker-compose up -d
```

### Stop the project

```
$ docker-compose down
```

### Access Minio interface

Enter in the browser http://localhost:9001/

### Access API

Enter in the browser http://localhost:8000/


### Access the Clickhouse client

```
$ docker-compose exec clickhouse clickhouse-client
```

## Assumptions & Decisions

- My understanding is that the client wants to get the number (number of trips over percentile 0.9) for any range time.
- There are rows in those parquet files where the trip distance is null. 
  - I would skip those one from the calculation
  - Apart from calculating the number of trips over the percentile, in a production environment I would provide the total of trips for the 
  specific time range to provide some context. 
- Based on the feedback I got from Alejandra:
  - I don't need to provide any kind of visualization.
  - The process would need to run on demand, repeatedly. 
  - We don't need to consider any external service to build the solution like GCP, AWS, ...
- Based on the nyc.gov page the trip data will be published monthly (with two months delay) instead of bi-annually. So we don't need 
a streaming real time process to ingest the data, because the data is available monthly with two months delay. It is mostly a batch
ingestion process. Even though since the client will require to run the process on-demand, ad-hoc we need to be able to query "on real time"
the parquet files stored in the ny taxis server.
- If we know in advance that the client wants to be able to do ad hoc requests over this data, we can precompute that parquet files so we can store the data in 
our servers in advance (data lake with MinIO or physically stored in Clickhouse or another OLAP) so the jobs will be faster.
- I decided to choose JSON format output for the API because it is lightweight data-interchange format that is easy for humans to read and easy for third party 
services to parse.
- If I would decide to store physically the data in Clickhouse or a similar datawarehouse I would probably store some extra columns apart from the measure
trip distance to be able to give to the client the same report by grouped by vendor_id or location (in which the taximeter was engaged or where it was disengaged).
- Probably I would partition the parquet files in MinIO and Clickhouse fact table by year month day extracted from tpep_pickup_datetime

### Solution and tech stack: 

#### Tech stack

- FastAPI
- MinIO. is a powerful, highly scalable, and lightweight open-source object storage server designed for private cloud infrastructure. 
It provides Amazon S3-compatible storage functionality
- Clickhouse. It is a real-time analytics database management system designed for online analytical processing (OLAP) 
  - It could be DuckDB
  - It could be BigLake with BigQuery but this would require GCP. And we don't want to rely on external services from the feedback I got. 
- Python
- Docker and Docker compose 

#### Solution

Basically the idea is to interact with the parquet files through the API built on top of FastAPI. So parquet files can be 
downloaded into MinIO and Clickhouse is used to query directly the parquet files stored in MinIO. For repeated queries we wouldn't need 
to download again or request the files stored in the server that we don't own. 

##### API - FastAPI
- Based on this I implemented an API that receives as parameters a list of years and months. So in that way the client
can calculate the number for any range.
- In the API there are 3 endpoints:
  - download_parquet: It let us download a specific parquet file by specifying year and month
  - trips_over_percentile: It calculates the number of trips over the percentile by reading the files stored previously in Minio.
    This is useful, because in case the client wants to get the trips for a range specified previously calculated we don't need to 
    download the parquet files again.
  - download_multiple_parquet_and_get_trips: It downloads the parquet files specified by the client and calculates the number of trips over the percentile by reading the files download and stored in Minio.
- In order to improve the performance of downloading the parquet files to store it in MinIO I implemented different improvements:
  - since the server supports range requests I implemented the download functionality with a couple of improvements:
    - Download files in parallel by gathering the different "download tasks (files)"
    - For each file, download it in chunks.
      - The chunk size by default is 10,000,000 bytes (10 MB). This is because the Content-Length of the file I have analyzed  is 50 MB more or less. For large 
      files a chunk size of 10MB is fine. By configuring this the file could be downloaded in 5 chunks of 10 MB each. Since in the API it is available a parameter
      to define the size of the chunk we could play with it. 
  - since the server is using the HTTP/2 protocol I configured http2=True because:
    - HTTP/2 allows multiple requests to be multiplexed over a single connection, reducing the need for multiple connections and improving resource utilization.
    - By reusing a single TCP connection, HTTP/2 minimizes connection setup and teardown time.
- We need to consider that the server from which we get the parquet files could return HTTP/2 403 Forbidden Error, so we would need to consider to implement
retry mechanism like exponential backoff, etc 
- We should also consider possible timeouts

##### MinIO

We could partition the parquet files stored in MinIO by date and use an open table format like Delta Lake in top of the parquet files. By using Delta Lake 
we would get some interesting additional capabilities apart from the columnar storage and compression that parquet already provides:
 - File Listing and Skipping: Delta Lake stores metadata statistics in the transaction log, allowing query engines to skip over unnecessary files and 
improve query performance. Parquet files require reading all files and gathering statistics before running a query, 
there are expensive footer reads to gather statistics for file skipping
 - Z-Order Indexing: Delta Lake supports z-order indexing, which improves query performance by allowing query engines to 
skip over unnecessary files and columns. Parquet files do not have this feature.
 - Schema evolution
 - etc 
The raw parquet files extracted from the trip data are stored by the app in the bucket ny-yellow-taxis inside the path ny-yellow-taxis/bronze.

##### Clickhouse 
Clickhouse allow us to execute queries over Parquet files stored in a storage system like s3. In this case I am using MinIO in my local environment
since MinIO is compatible with S3. 
I have used the quantile function from Clickhouse, that based on the official documentation the result is non-deterministic. I could use quantileExact 
that calculates the exact quantile, but it can be slower and more memory-intensive than the quantile function.

```
select trip_distance from s3('http://minio:9000/ny-yellow-taxis/bronze/yellow_tripdata_2024-09.parquet', 'minioadmin', 'minioadmin', 'Parquet');
```


This is a common requirement in data lake use cases where ad hoc analysis is required. Since the client want to be able to query several range times
I make use of  glob patterns, allowing subsets of files to be selected. This lets parallelization reads.
A potential improvement would be to stored physically the data into Clickhouse, event though parquet is a data format for file distribution, it will not be 
as efficient for querying as CH with its optimizations. We could partition the fact table by year, month, day etc


## Unit and Integration tests.

I didn't consider for this kind of challenge to expend time on implementing unit and integration tests. From the conversations and questions I have asked 
to Tinybird engineers my assumption is that you want to see what are my approaches, how I think, etc instead of expending time on implementation details.


## Final Thoughts

- We could also decide to query directly the data stored in the external server by doing from Clickhouse

  ```
  select trip_distance from s3('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet', 'Parquet');
  ```
  
  This is fine for  ad hoc analysis or prototyping. However, for production workflows, where: we need to query the data repeatedly, 
  ensure data reliability or enrich the data storing the files in own server in MinIO (or similar data lake) gives us control, scalability, etc

- I didn't implement any kind of authentication in the API. I could add simple token authentication but I decided not to expend time on this. 
- - I didn't expect time on doing validations through Pydantic of the parameters of the different API endpoints. 
- In a Production environment it could be interesting to have a QA tool in place like Great Expectation to validate the quality of the data.
- In a Production environment if we want to be able to orchestrate all the process and since the data is available monthly with a delay of two months
we could have an orchestrator like Airflow to orchestrate the workflow, Spark for doing data cleaning like dropping unnecessary columns, unifying schemas, handling missing values,
drop outliers (eg drop rows where pickup time is no earlier than dropff time) etc and ingest the data into the data lake partitioned by date. 
By having this setup we could schedule to run the load of the parquet files monthly so when the user would need to request the data it would be already available
in the data lake and Clickhouse. 
