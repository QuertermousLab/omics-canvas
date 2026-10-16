# Export an R object (.rds) into plain files that convert.py turns into viewer files.
#
#   Rscript rds_export.R IN.rds OUT_DIR [ASSAY]
#
# Seurat (v3-v5), SingleCellExperiment / SpatialExperiment:
#   export.json        what was written (type, assays, embeddings, spatial images, accessibility, ...)
#   x.bin i.bin p.bin  the first assay's cells x features matrix as R's dgCMatrix (features x cells,
#                      column-compressed): float64 values, int32 row indices, int32 column pointers
#   genes.txt cells.txt
#   assay_<name>/      the same four files for every other assay (multiome: RNA, ATAC peaks, chromVAR, ...)
#   fragments.tsv      Signac fragment files: path, cell, barcode (the barcode in that file)
#   annotation.tsv     Signac gene annotation (exon rows: seqnames start end strand gene_name gene_biotype type tx_id)
#   motifs.tsv         Signac motif PWMs: id, name, then the A, C, G, T rows (comma-separated)
#   meta.csv           per-cell annotations (first column "cell"), factor levels in export.json
#   emb_<name>.csv     2-D embeddings (cell, x, y)
#   spatial_<img>.csv  tissue coordinates (cell, x, y) per image
# data.frame / data.table / sceptre_object (CRISPR screen results):
#   table.tsv          the table (sceptre_object: its discovery result)
#
# Progress lines ("@@progress <percent> <message>") are read by the hub to show progress.

suppressPackageStartupMessages({
  library(methods)
  library(Matrix)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: rds_export.R IN.rds OUT_DIR [ASSAY]")
src <- args[[1]]
out <- args[[2]]
assay_arg <- if (length(args) >= 3) args[[3]] else ""
dir.create(out, showWarnings = FALSE, recursive = TRUE)

progress <- function(pct, msg) { cat(sprintf("@@progress %d %s\n", as.integer(pct), msg)); flush(stdout()) }
has <- function(pkg) requireNamespace(pkg, quietly = TRUE)
json_str <- function(x) {
  if (is.null(x) || length(x) == 0) return("null")
  if (is.list(x)) {
    if (!is.null(names(x)) && all(nzchar(names(x)))) {
      return(paste0("{", paste(sprintf("%s:%s", vapply(names(x), json_str, ""), vapply(x, json_str, "")), collapse = ","), "}"))
    }
    return(paste0("[", paste(vapply(x, json_str, ""), collapse = ","), "]"))
  }
  if (is.logical(x) && length(x) == 1) return(if (is.na(x)) "null" else tolower(as.character(x)))
  if (is.numeric(x) && length(x) == 1) return(if (is.na(x)) "null" else format(x, digits = 15, scientific = FALSE))
  if (length(x) == 1) {
    s <- gsub("\\\\", "\\\\\\\\", as.character(x))
    s <- gsub('"', '\\\\"', s)
    s <- gsub("\n", "\\\\n", s)
    s <- gsub("\t", "\\\\t", s)
    return(paste0('"', s, '"'))
  }
  paste0("[", paste(vapply(as.list(x), json_str, ""), collapse = ","), "]")
}
write_json <- function(obj, path) writeLines(json_str(obj), path, useBytes = TRUE)

write_vec <- function(v, path, size) {
  con <- file(path, "wb")
  on.exit(close(con))
  n <- length(v)
  step <- 50000000
  start <- 1
  while (start <= n) {
    end <- min(n, start + step - 1)
    writeBin(v[start:end], con, size = size)
    start <- end + 1
  }
}

as_dgc <- function(m) {
  if (inherits(m, "dgCMatrix")) return(m)
  if (inherits(m, "DelayedArray") || inherits(m, "DelayedMatrix")) m <- as(m, "dgCMatrix")
  m <- suppressWarnings(tryCatch(as(m, "CsparseMatrix"), error = function(e) Matrix::Matrix(as.matrix(m), sparse = TRUE)))
  if (!inherits(m, "dgCMatrix")) m <- suppressWarnings(as(as(m, "generalMatrix"), "dMatrix"))
  if (!inherits(m, "dgCMatrix")) m <- suppressWarnings(as(m, "dgCMatrix"))
  m
}

write_matrix <- function(m, layer, dir = out, what = "") {
  m <- as_dgc(m)
  dir.create(dir, showWarnings = FALSE, recursive = TRUE)
  progress(35, sprintf("Writing %s%s matrix: %d features x %d cells, %s non-zero", what, layer, nrow(m), ncol(m), format(length(m@x), big.mark = ",")))
  sample_x <- head(m@x, 1000000)
  integer <- length(sample_x) == 0 || (all(sample_x >= 0) && all(abs(sample_x - round(sample_x)) < 1e-6))
  write_vec(as.double(m@x), file.path(dir, "x.bin"), 8)
  write_vec(as.integer(m@i), file.path(dir, "i.bin"), 4)
  write_vec(as.integer(m@p), file.path(dir, "p.bin"), 4)
  writeLines(as.character(rownames(m)), file.path(dir, "genes.txt"), useBytes = TRUE)
  writeLines(as.character(colnames(m)), file.path(dir, "cells.txt"), useBytes = TRUE)
  list(layer = layer, nGenes = nrow(m), nCells = ncol(m), nnz = length(m@x), integer = integer)
}

write_meta <- function(df, cells) {
  df <- as.data.frame(df, stringsAsFactors = FALSE)
  types <- list()
  for (col in names(df)) {
    v <- df[[col]]
    if (is.factor(v)) {
      types[[col]] <- list(type = "factor", levels = as.list(levels(v)))
      df[[col]] <- as.character(v)
    } else if (is.logical(v)) {
      types[[col]] <- list(type = "logical")
      df[[col]] <- ifelse(is.na(v), NA, ifelse(v, "TRUE", "FALSE"))
    } else if (is.numeric(v)) {
      types[[col]] <- list(type = "numeric")
    } else if (is.list(v) || is.matrix(v) || is.data.frame(v)) {
      df[[col]] <- NULL
      next
    } else {
      df[[col]] <- as.character(v)
      types[[col]] <- list(type = "character")
    }
  }
  df <- cbind(data.frame(cell = cells, stringsAsFactors = FALSE), df)
  utils::write.csv(df, file.path(out, "meta.csv"), row.names = FALSE, na = "")
  types
}

write_xy <- function(cells, x, y, path) {
  utils::write.csv(data.frame(cell = cells, x = as.numeric(x), y = as.numeric(y)), path, row.names = FALSE)
}

safe_name <- function(s) gsub("[^A-Za-z0-9_.-]+", "_", s)

# A tissue image (array h x w [x channels], values 0-1 or 0-255) -> raw uint8, row-major h x w x c, for
# image_tiles.py. Returns list(file, dim) or NULL.
write_raster <- function(a, path) {
  if (is.null(a) || length(dim(a)) < 2 || prod(dim(a)) == 0) return(NULL)
  d <- dim(a)
  if (length(d) == 2) { a <- array(a, c(d, 1)); d <- dim(a) }
  v <- as.numeric(a)
  if (max(v, na.rm = TRUE) <= 1) v <- v * 255
  v[is.na(v)] <- 0
  a <- array(as.integer(pmin(pmax(round(v), 0), 255)), d)
  # R arrays are column-major: put the channel first, then column, then row so the bytes read as h x w x c
  con <- file(path, "wb"); on.exit(close(con))
  writeBin(as.raw(aperm(a, c(3, 2, 1))), con)
  list(file = basename(path), dim = as.integer(d))
}
# slots are attributes: this also works for classes whose package (e.g. Signac) is not installed
slot_or <- function(obj, name, default = NULL) {
  v <- attr(obj, name, exact = TRUE)
  if (is.null(v)) v <- tryCatch(methods::slot(obj, name), error = function(e) NULL)
  if (is.null(v)) default else v
}

# ---------------------------------------------------------------- Seurat

seurat_layer <- function(obj, assay, layer) {
  a <- obj@assays[[assay]]
  # compare class names: inherits() needs the class definition, i.e. Signac for a ChromatinAssay
  if ("Assay5" %in% class(a)) {
    if (!has("SeuratObject")) stop("a Seurat v5 object needs SeuratObject >= 5")
    lnames <- names(a@layers)
    hits <- lnames[lnames == layer | startsWith(lnames, paste0(layer, "."))]
    if (!length(hits)) return(NULL)
    if (length(hits) > 1) a <- SeuratObject::JoinLayers(a, layers = layer)
    m <- SeuratObject::LayerData(a, layer = layer)
    if (is.null(m) || !length(m)) return(NULL)
    return(m)
  }
  m <- slot_or(a, layer)
  if (is.null(m) || !length(m) || nrow(m) == 0) return(NULL)
  m
}

is_chromatin <- function(a) !is.null(attr(a, "ranges", exact = TRUE)) && !is.null(attr(a, "fragments", exact = TRUE))

assay_type <- function(name, a, features) {
  if (grepl("chromvar", name, ignore.case = TRUE) || (length(features) && mean(grepl("^MA[0-9]{4}\\.[0-9]+", head(features, 200))) > 0.8)) return("chromvar")
  if (is_chromatin(a) || mean(grepl("^(chr)?[0-9XYM]+[-:_][0-9]+[-_][0-9]+$", head(features, 200))) > 0.8) return("peaks")
  if (grepl("activity", name, ignore.case = TRUE)) return("activity")
  if (grepl("^ADT|protein|antibody", name, ignore.case = TRUE)) return("protein")
  "rna"
}

# accessibility pieces of the first Signac ChromatinAssay: fragment files, gene annotation, motifs
export_chromatin <- function(obj, cells) {
  res <- list()
  for (name in names(obj@assays)) {
    a <- obj@assays[[name]]
    if (!is_chromatin(a)) next
    frs <- attr(a, "fragments", exact = TRUE)
    rows <- list()
    for (fr in frs) {
      path <- slot_or(fr, "path"); fc <- slot_or(fr, "cells")
      if (is.null(path) || !file.exists(path)) { cat(sprintf("  fragment file not found: %s\n", path)); next }
      if (is.null(fc)) next
      keep <- names(fc) %in% cells
      rows[[length(rows) + 1]] <- data.frame(path = path, cell = names(fc)[keep], barcode = unname(fc)[keep])
    }
    if (length(rows)) {
      utils::write.table(do.call(rbind, rows), file.path(out, "fragments.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
      res$fragments <- length(rows)
    }
    an <- attr(a, "annotation", exact = TRUE)
    if (!is.null(an) && length(an)) {
      names(an) <- NULL  # exon ids repeat across transcripts: no row names
      df <- tryCatch(as.data.frame(an), error = function(e) { cat("  annotation not exported:", conditionMessage(e), "\n"); NULL })
      if (!is.null(df)) {
        cols <- intersect(c("seqnames", "start", "end", "strand", "gene_name", "gene_biotype", "type", "tx_id"), names(df))
        utils::write.table(df[, cols], file.path(out, "annotation.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
        g <- tryCatch(unique(GenomeInfoDb::genome(an)), error = function(e) NA)
        res$genome <- as.character(g[!is.na(g)][1])
      }
    }
    mo <- attr(a, "motifs", exact = TRUE)
    if (!is.null(mo)) {
      pwm <- slot_or(mo, "pwm"); mn <- slot_or(mo, "motif.names")
      if (length(pwm)) {
        lines <- vapply(names(pwm), function(id) {
          m <- as.matrix(pwm[[id]]); rn <- rownames(m)
          ord <- if (all(c("A", "C", "G", "T") %in% rn)) m[c("A", "C", "G", "T"), , drop = FALSE] else m[1:4, , drop = FALSE]
          paste(c(id, if (!is.null(mn[[id]])) as.character(mn[[id]]) else id, apply(ord, 1, paste, collapse = ",")), collapse = "\t")
        }, "")
        writeLines(lines, file.path(out, "motifs.tsv"), useBytes = TRUE)
        res$motifs <- length(lines)
      }
    }
    res$assay <- name
    break
  }
  res
}

export_seurat <- function(obj) {
  if (has("SeuratObject")) obj <- tryCatch(suppressWarnings(SeuratObject::UpdateSeuratObject(obj)), error = function(e) obj)
  assays <- names(obj@assays)
  # the main (first) assay: RNA, else the active one — unless that is a control or a handful of features (Xenium objects
  # often have niche, BlankCodeword, ControlProbe … active): then the largest expression assay. Controls go last.
  control <- function(a) grepl("codeword|control|blank|genomic|negative|unassigned|deprecated|^niche", a, ignore.case = TRUE)
  nfeat <- function(a) tryCatch(nrow(obj@assays[[a]]), error = function(e) 0L)
  usable <- function(a) !control(a) && nfeat(a) >= 50
  assay <- if (nzchar(assay_arg)) assay_arg else if ("RNA" %in% assays) "RNA" else obj@active.assay
  if (!nzchar(assay_arg) && !usable(assay)) {
    expr <- Filter(usable, assays)
    if (length(expr)) assay <- expr[[which.max(vapply(expr, nfeat, numeric(1)))]]
  }
  assays <- c(Filter(function(a) !control(a), assays), Filter(control, assays))
  if (!assay %in% assays) stop(sprintf("assay %s not found (assays: %s)", assay, paste(assays, collapse = ", ")))
  progress(20, sprintf("Seurat object, assays: %s (first: %s)", paste(assays, collapse = ", "), assay))
  pick <- function(a) {
    layer <- "counts"; m <- seurat_layer(obj, a, "counts")
    if (is.null(m)) { layer <- "data"; m <- seurat_layer(obj, a, "data") }
    list(m = m, layer = layer)
  }
  p <- pick(assay)
  if (is.null(p$m)) stop(sprintf("assay %s has neither counts nor data", assay))
  info <- write_matrix(p$m, p$layer, what = paste0(assay, " "))
  cells <- colnames(p$m)
  all_assays <- list(list(name = assay, dir = "", class = class(obj@assays[[assay]])[1], type = assay_type(assay, obj@assays[[assay]], rownames(p$m)), matrix = info))
  for (a in setdiff(assays, assay)) {
    q <- tryCatch(pick(a), error = function(e) { cat(sprintf("  assay %s could not be read: %s\n", a, conditionMessage(e))); list(m = NULL) })
    if (is.null(q$m)) { cat(sprintf("  assay %s: no counts or data, skipped\n", a)); next }
    d <- paste0("assay_", safe_name(a))
    inf <- write_matrix(q$m, q$layer, dir = file.path(out, d), what = paste0(a, " "))
    all_assays[[length(all_assays) + 1]] <- list(name = a, dir = d, class = class(obj@assays[[a]])[1], type = assay_type(a, obj@assays[[a]], rownames(q$m)), matrix = inf)
  }

  progress(60, "Writing cell annotations")
  meta <- obj@meta.data[cells, , drop = FALSE]
  types <- write_meta(meta, cells)

  embeddings <- list()
  for (r in names(obj@reductions)) {
    e <- slot_or(obj@reductions[[r]], "cell.embeddings")
    if (is.null(e) || ncol(e) < 2) next
    write_xy(rownames(e), e[, 1], e[, 2], file.path(out, sprintf("emb_%s.csv", safe_name(r))))
    embeddings[[length(embeddings) + 1]] <- list(name = r, file = sprintf("emb_%s.csv", safe_name(r)))
  }

  progress(75, "Writing tissue coordinates")
  images <- list()
  for (img_name in names(obj@images)) {
    img <- obj@images[[img_name]]
    cls <- class(img)[1]
    sf <- slot_or(img, "scale.factors")
    spot <- tryCatch(as.numeric(sf$spot), error = function(e) NA)
    xy <- NULL
    b <- slot_or(img, "boundaries")
    if (!is.null(b) && !is.null(b$centroids)) {
      cc <- slot_or(b$centroids, "coords")
      cn <- slot_or(b$centroids, "cells")
      # VisiumV2 keeps its spot centroids as (imagerow, imagecol); other FOVs as (x, y)
      if (!is.null(cc)) xy <- if (cls == "VisiumV2") data.frame(cell = cn, x = cc[, 2], y = cc[, 1]) else data.frame(cell = cn, x = cc[, 1], y = cc[, 2])
    }
    if (is.null(xy)) {
      co <- slot_or(img, "coordinates")
      if (!is.null(co)) {
        if (all(c("imagerow", "imagecol") %in% names(co))) xy <- data.frame(cell = rownames(co), x = co$imagecol, y = co$imagerow)
        else if (all(c("x", "y") %in% names(co))) xy <- data.frame(cell = rownames(co), x = co$x, y = co$y)
      }
    }
    if (is.null(xy) && has("SeuratObject")) {
      tc <- tryCatch(SeuratObject::GetTissueCoordinates(img), error = function(e) NULL)
      if (!is.null(tc)) {
        cellcol <- if ("cell" %in% names(tc)) tc$cell else rownames(tc)
        xcol <- intersect(c("x", "imagecol"), names(tc))[1]; ycol <- intersect(c("y", "imagerow"), names(tc))[1]
        if (!is.na(xcol) && !is.na(ycol)) xy <- data.frame(cell = cellcol, x = tc[[xcol]], y = tc[[ycol]])
      }
    }
    if (is.null(xy)) { cat(sprintf("  image %s (%s): no coordinates found\n", img_name, cls)); next }
    f <- sprintf("spatial_%s.csv", safe_name(img_name))
    write_xy(xy$cell, xy$x, xy$y, file.path(out, f))
    img_assay <- tryCatch(as.character(slot_or(img, "assay")), error = function(e) "")
    # the tissue image (Visium keeps the low- or high-resolution PNG; coordinates are full-resolution pixels)
    raster <- tryCatch(write_raster(slot_or(img, "image"), file.path(out, sprintf("image_%s.u8", safe_name(img_name)))),
                       error = function(e) { cat("  image raster not exported:", conditionMessage(e), "\n"); NULL })
    if (!is.null(raster)) {
      raster$lowres <- tryCatch(as.numeric(sf$lowres), error = function(e) NA)
      raster$hires <- tryCatch(as.numeric(sf$hires), error = function(e) NA)
    }
    images[[length(images) + 1]] <- list(name = img_name, class = cls, file = f, spot = spot, assay = img_assay, cells = nrow(xy), raster = raster)
    cat(sprintf("  image %s (%s): %d positions\n", img_name, cls, nrow(xy)))
  }
  progress(85, "Looking for fragments, gene annotation and motifs")
  chromatin <- tryCatch(export_chromatin(obj, cells), error = function(e) { cat("  accessibility export failed:", conditionMessage(e), "\n"); list() })
  list(type = "seurat", assay = assay, assays = all_assays, matrix = info, meta = types, chromatin = chromatin,
       embeddings = embeddings, images = images, project = tryCatch(as.character(obj@project.name), error = function(e) ""))
}

# ---------------------------------------------------------------- SingleCellExperiment

export_sce <- function(obj) {
  if (!has("SummarizedExperiment")) stop("SummarizedExperiment is needed to read this object")
  an <- SummarizedExperiment::assayNames(obj)
  layer <- if ("counts" %in% an) "counts" else an[[1]]
  progress(20, sprintf("%s, assay %s (assays: %s)", class(obj)[1], layer, paste(an, collapse = ", ")))
  m <- SummarizedExperiment::assay(obj, layer)
  if (is.null(colnames(m))) colnames(m) <- paste0("cell", seq_len(ncol(m)))
  if (is.null(rownames(m))) rownames(m) <- paste0("gene", seq_len(nrow(m)))
  info <- write_matrix(m, layer)
  cells <- colnames(m)
  progress(60, "Writing cell annotations")
  types <- write_meta(as.data.frame(SummarizedExperiment::colData(obj)), cells)
  embeddings <- list()
  if (has("SingleCellExperiment")) {
    for (r in SingleCellExperiment::reducedDimNames(obj)) {
      e <- SingleCellExperiment::reducedDim(obj, r)
      if (ncol(e) < 2) next
      write_xy(cells, e[, 1], e[, 2], file.path(out, sprintf("emb_%s.csv", safe_name(r))))
      embeddings[[length(embeddings) + 1]] <- list(name = r, file = sprintf("emb_%s.csv", safe_name(r)))
    }
  }
  images <- list()
  if (inherits(obj, "SpatialExperiment") && has("SpatialExperiment")) {
    sc <- SpatialExperiment::spatialCoords(obj)
    samples <- tryCatch(as.character(obj$sample_id), error = function(e) rep("tissue", length(cells)))
    if (length(samples) != length(cells)) samples <- rep("tissue", length(cells))
    imgs <- tryCatch(SpatialExperiment::imgData(obj), error = function(e) NULL)
    # one section per sample (laid out side by side), each with its first image (spatialCoords are full-resolution pixels)
    for (s in unique(samples)) {
      k <- samples == s
      f <- sprintf("spatial_%s.csv", safe_name(s))
      write_xy(cells[k], sc[k, 1], sc[k, 2], file.path(out, f))
      raster <- NULL
      if (!is.null(imgs) && nrow(imgs)) {
        r <- which(as.character(imgs$sample_id) == s)[1]
        if (!is.na(r)) raster <- tryCatch({
          ras <- SpatialExperiment::imgRaster(imgs$data[[r]])
          rgb <- grDevices::col2rgb(as.vector(ras), alpha = TRUE)  # 4 x (h*w), raster is row-major
          a <- array(t(rgb), c(ncol(ras), nrow(ras), 4))           # w x h x 4 (column-major fill of the row-major pixels)
          x <- write_raster(aperm(a, c(2, 1, 3)), file.path(out, sprintf("image_%s.u8", safe_name(s))))
          x$scale <- as.numeric(imgs$scaleFactor[r])
          x
        }, error = function(e) { cat("  image raster not exported:", conditionMessage(e), "\n"); NULL })
      }
      images[[length(images) + 1]] <- list(name = s, class = "SpatialExperiment", file = f, spot = NA, assay = "", cells = sum(k), raster = raster)
    }
  }
  list(type = "sce", assay = layer, matrix = info, meta = types, embeddings = embeddings, images = images)
}

# ---------------------------------------------------------------- tables (CRISPR results)

export_table <- function(df, what) {
  df <- as.data.frame(df, stringsAsFactors = FALSE)
  for (col in names(df)) if (is.list(df[[col]])) df[[col]] <- vapply(df[[col]], function(v) paste(v, collapse = ","), "")
  progress(60, sprintf("Writing %s: %d rows x %d columns", what, nrow(df), ncol(df)))
  utils::write.table(df, file.path(out, "table.tsv"), sep = "\t", quote = FALSE, row.names = FALSE, na = "NA")
  list(type = "table", what = what, rows = nrow(df), columns = as.list(names(df)))
}

# ---------------------------------------------------------------- main

progress(2, sprintf("Reading %s", basename(src)))
obj <- readRDS(src)
cls <- class(obj)
progress(15, sprintf("Loaded object of class %s", paste(cls, collapse = "/")))

res <- if (inherits(obj, "Seurat")) {
  export_seurat(obj)
} else if (inherits(obj, "SingleCellExperiment") || inherits(obj, "SummarizedExperiment")) {
  export_sce(obj)
} else if (inherits(obj, "sceptre_object")) {
  dr <- slot_or(obj, "discovery_result")
  if (is.null(dr) || !nrow(dr)) dr <- slot_or(obj, "power_result")
  if (is.null(dr) || !nrow(dr)) stop("this sceptre_object has no discovery (or power) results; run run_discovery_analysis() first")
  export_table(dr, "sceptre discovery results")
} else if (is.data.frame(obj)) {
  export_table(obj, "data frame")
} else if (is.list(obj) && length(obj) && all(vapply(obj, is.data.frame, TRUE))) {
  export_table(obj[[1]], sprintf("data frame (first of %d in a list)", length(obj)))
} else {
  stop(sprintf("unsupported R object of class %s (expected Seurat, SingleCellExperiment, sceptre_object or data.frame)", paste(cls, collapse = "/")))
}
res$class <- as.list(cls)
write_json(res, file.path(out, "export.json"))
progress(90, "R export finished")
