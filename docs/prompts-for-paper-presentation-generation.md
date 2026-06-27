## Prompt 1 from User

Summarize the paper's background & motivation and design.

## Prompt 2 from User

Write a presentation slides for this paper in markdown for me:
1. three heading 1 sections: Background & Motivation, System Design, Evaluation.
2. don't number the headings
3. tell me which figure to include in the placeholder
4. not too much text in a single section
5. no speaker notes
6. refer to my personal style in the examples provided


## Example 1

```markdown
---
title: |
        GeminiFS\: A Companion File System for GPUs
subtitle: FAST '25
institute: (NICE Lab XMU & Huawei Theory Lab & SJTU)
author: Shi Qiu, Weinan Liu, Yifan Hu, Jianqin Yan, and Zhirong Shen, NICE Lab, Xiamen University; Xin Yao, Renhai Chen, and Gong Zhang, Huawei Theory Lab; Yiming Zhang, NICE Lab, Xiamen University and Shanghai Jiao Tong University
date: March 21, 2025
---

# Background & Motivation

## Increasing Storage Demand of GPU Workloads

- Massive datasets of model training
- KV cache offloading of LLM inference

## Existing GPU Storage Expansion Methods: Native

- One redundant copy, CPU-centric, heavy synchronization cost

![](./gpu-direct-gds.drawio.png){width=45%}

## Existing GPU Storage Expansion Methods: CPU-initiated GPU Direct Storage

- CPU-centric, heavy synchronization cost

![](./gpu-direct-gds.drawio.png){width=40%}

## Existing GPU Storage Expansion Methods: GPU-initiated GPU Direct Storage

- GPU-centric, no filesystem semantic

![](./gpu-direct-bam.drawio.png){width=40%}

## Existing GPU Storage Expansion Methods: Conclusion

- Existing GPU storage expansion methods either
   - have poor performance, or
   - high CPU overhead, or
   - lack of filesystem abstraction
- Fail to meet the the high parallelism and data sharing requirements of common training/inference scenarios such as accessing input data and sharing KV cache

## Motivation

![](./gpu-direct-geminifs.drawio.png){width=25%}

-  A lightweight GPU file system called Companion File System, which *coexists* with the host file system.
-  On the host, the host file system is used for:
   -  file management (e.g., created, moved, and deleted)
   -  integrating necessary metadata into the files in order to share with the GPU
-  On the GPU:
   -  retrieve the metadata
   -  lightweight file system abstraction

## Storage Access in GPU Workloads

![](./pattern.png)

- Short-term data needs no persistence.
- Long-term data is append-only.
- Most storage access has predictable patterns.
- Data needs to be shared across multiple GPU processes.

## Overhead Analysis

![GPUfs: Native CPU-centric Approach; 4KB I/O; ZhiTi: 15us, P5800X: 4us](./lat-gpu-threads.png)

- When the number of GPU threads is low, the I/O latency is higher than 190μs on both storage devices
  - Software stack overhead accounts for over 90% of the overall I/O latency
- When the number of GPU threads increases, the contention overhead is also critical.

## Overhead Analysis

![GDS: CPU-initiated GDS](./lat-gpu-threads.png)

- As the IO batch size increases, the average and tail latencies decrease but still remain relatively high (around 160 μs).

## Basic requirements of a file system

- Metadata maintainance: files, directories
- Transaction processing for consistency
- LBA translation
- Unified interface for upper-level applications
- Data caching

## Challenges

1. Metadata synchronization
   - It's hard to fully utilize GPU parallelism in metadata synchronization
   - Metadata is usually exclusively managed in host kernel
2. Device driver limitations
   - coexisting host/GPU filesystems and NVMe drivers
3. GPU page cache efficiency
   - Constructing page cache on GPU results in memory redundancy and increases synchronization issue. 
   - Traditional page cache design cannot meet the shareable access and high parallelism requirements of GPU
4. GPU programming complexity

# Design of GeminiFS

## System Architecture

![](./overview.png){width=55%}

- GPU Virtual Disk Format (GVDK) for metadata embedding
- CPU/GPU Shared NVMe Driver
- GPU-specific Page Cache
- POSIX-compatible programming library for GPU applications

## CPU-Bypassing via Metadata Embedding

![](layout.png){width=55%}

- GPU Virtual Disk Format (GVDK)
  - **Embedded Metadata**
    - file specific metadata: type, size, timestamp, block size
  - **Embedded Block Map**
    - Two-level mapping
    - Dirty bitmap records page allocation status

## CPU/GPU Shared NVMe Driver

- Admin QP in host kernel
- I/O QPs in both host/GPU memory
- For GPU I/O QPs, a high performance lock-free queue design is adopted from BaM.

## GPU-specific Page Cache

- As different processes have different GPU memory space, the **GPU page cache cannot be naturally shared**.
  - A host kernel module maintains the mapping between the file offset to the memory address of GPU page.
  - When another process tries to access the same page of the file, it utilizes CUDA IPC to enable zero-copy sharing.
- Reduced lock contention
  - Acquiring lock at warp level instead of thread-level
  - A concurrent hash table combined with doubly-linked list is used to maintain page cache mappings  

## GPU Programming Model

![](program-model.png)

- A subset of POSIX-compatible filesystem APIs are provided to GPU applications
- CPU-side APIs: `SNVME_init`, `G_Open`, `G_close`
- GPU-side APIs: `G_Read`, `G_Write`

# Evaluation

## Environment Setup

- **System settings**
  - Intel Xeon 5416S, 16-cores
  - 512GB host memory
  - Linux kernel 5.15
  - NVIDIA GPU with 80GB HBM
    - PCIe4x16, 64GB/s bidirectional
    - 1935 GB/s memory bandwidth
  - Intel Optane 5800X with EXT4 file system mounted.
    - 64 I/O QPs on host
    - 32 I/O QPs on GPU
- **Baselines**
  - **GPUfs**: a CPU-centric native method. (4 CPU threads are used to serve IO request from the GPU)
  - **NVIDIA GDS (cuFile library)**: a CPU-centric direct storage method
  - **BaM**: GPU-centric solution without file system semantic

## Bandwidth & Latency of 4KB read

![4KB page size](bw-lat.png)

- Near performance with BaM, which has not file abstraction
- Fully saturate device bandwidth

## Performance of GPU Page Cache: Prefech

![64KB page size / 1GB page cache / Sequential read on a 20GB file](gpu-page-cache.png)

- Prefech is needed

## Performance of GPU Page Cache: Number of Warps

![](nwarp.png)

## Performance of GPU Page Cache: Page size

![128 warps](page-size.png)

## GPT2-124M training

![](param.png){width=70%}

![](train.png){width=70%}

- Each training step consists of a forward pass and a backward pass
  - A checkpoint is saved after each step.
```

## Example 2

```markdown
---
title: |
        ScaleXFS\: Getting scalability of XFS back on the ring
subtitle: FAST '22
institute: (KAIST)
author: Dohyun Kim, Kwangwon Min, Joontaek Oh, and Youjip Won
date: Jan 17, 2025
---

# Background and Motivation

## The number of cores is increasing.

![](cores.png){ width=70% }

---

## The devices are getting faster and quicker.

![](ssds.png){ width=70% }

---

## XFS

![](xfs.png)

- XFS uses B+tree to manage free inodes, dentries.
- XFS adopts differential logging

---

## XFS: In-memory Logging

![In-memory Logging](update.png)

- The filesystem is responsible for two types of operations:
  - *In-memory Logging*: Updating the state of the in-memory filesystem
    - creat(), unlink()
  - *On-disk Logging*: Synchronizing the in-memory filesystem state to the disk
    - fsync()

---

## XFS: On-disk Logging

![On-disk Logging](journal.png)

- The filesystem is responsible for two types of operations:
  - *In-memory Logging*: Updating the state of the in-memory filesystem
    - creat(), unlink()
  - *On-disk Logging*: Synchronizing the in-memory filesystem state to the disk
    - fsync()

---

## Differential Logging

![](diff-logging.png)

- Differential Logging allows multiple concurrent in-memory metadata updates.
- Each metadata update would end up with add/merging log data into log list
  - Acquiring a spin lock on log-list

---

## Scalability Analysis: Throughput

![varmail-ptd, dbench, mdtest](scalability-1.png)

- Throughput is normalized against the throughput with 4 cores.

---

## Scalability Analysis: Latency

![](scalability-2.png){ width=80% }

---

## Scalability Analysis: Lock waiting time

![905P: Ultra-Low-Latency SSD](lock-time.png)

- As the performance of SSD improves, the in-memory logging becomes the bottleneck

---

## Scalability Analysis: Component Analysis

![](component-1.png)

- In-memory logging begins to dominate the latency of metadata operations.

---

## Scalability Analysis: Component Analysis

![](component-2.png)

- In-memory logging begins to dominate the latency of metadata operations.
- Within in-memory logging, the overhead mainly comes from lock contention on log list.

---

## Contention on the Log List

![](contention-1.png)

- Multiple concurrent in-memory metadata operations competes for the lock.
- In-memory logging contends with on-disk logging.

---

## Contention on the Log List

![](contention-2.png)

- In-memory logging threads use two locks.
  - Firstly acquire R-lock when checking
  - Then acquire X-lock when merging and inserting
- On-disk logging acquires W-lock when journaling

---

## Contention on the Log List

![](contention-3.png)

- When on-disk logging holds the write lock, **all in-memory loggings are blocked**.

---

## Contention on the Log List

![](contention-4.png)

- Although in-memory loggings hold the R-lock, **the multiple in-memory loggings are serialized**.

# Design of ScaleXFS

## Double Log List

![](double-log-list.png)

- When on-disk logging holds write lock on one list, the in-memory loggings access another available list.

---

## Per-core In-memory Logging

![](per-core.png)

---

### Merging Mechanism

![](merge.png)

---

## Optimization: Strided Space Counting

![Stride length: 5](counter.png)

- A space counter is used in XFS to estimate the size of disk space to be occupied by the log list.
  - Protected by a global spinlock
- In ScaleXFS, per-core space counter (L) and per-core strided counter (S), global counter (G)

# Evaluation

## Environment Setup

- 4-sockets Intel Xeon Platinum 8276, 112 cores in total.
- 512GB DRAM
- NVMe SSD: Intel Optane 905P
- Baseline: XFS
- Scale-XFS:
  - S-XFS-D: double log list
  - S-XFS-DP: + per-core log list
  - S-XFS-DPS: + per-core strided space counter

![Workload characteristics](benchmarks.png)

---

## Lock Contention

![](eval-lock.png)

---

## Latency of create, unlink, fsync

![](eval-latency.png)

---

## Throughput

![](eval-throughput.png)

```