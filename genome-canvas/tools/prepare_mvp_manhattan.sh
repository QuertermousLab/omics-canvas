#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "Usage: $0 SOURCE_SUMSTATS_GZ HG19_TO_HG38_CHAIN OUTPUT_GWAS_GZ [THREADS]" >&2
  exit 2
fi

source_file="$1"
chain_file="$2"
output_file="$3"
threads="${4:-4}"

[[ -r "$source_file" ]] || { echo "Cannot read source: $source_file" >&2; exit 1; }
[[ -r "$chain_file" ]] || { echo "Cannot read chain: $chain_file" >&2; exit 1; }
[[ "$output_file" == *.gwas.gz ]] || { echo "Output must end in .gwas.gz" >&2; exit 2; }
[[ "$threads" =~ ^[1-9][0-9]*$ ]] || { echo "THREADS must be a positive integer" >&2; exit 2; }

for program in gzip awk liftOver sort tabix mkfifo; do
  command -v "$program" >/dev/null 2>&1 || { echo "Missing required program: $program" >&2; exit 1; }
done
if [[ -x /usr/bin/bgzip ]]; then
  bgzip_command=(/usr/bin/bgzip -@ "$threads" -c)
elif command -v bgzip >/dev/null 2>&1; then
  bgzip_command=(bgzip -c)
else
  echo "Missing required program: bgzip" >&2
  exit 1
fi

output_dir="$(cd "$(dirname "$output_file")" && pwd)"
output_name="$(basename "$output_file")"
work_dir="$(mktemp -d /tmp/genome-canvas-mvp-liftover.XXXXXX)"
bed_pipe="$work_dir/hg19.bed.pipe"
mapped_bed="$work_dir/mapped.hg38.bed"
unmapped_bed="$work_dir/unmapped.hg19.bed"
temporary_output="$output_dir/.${output_name}.partial.gwas.gz"

cleanup() {
  rm -f "$bed_pipe" "$temporary_output" "${temporary_output}.tbi"
  rm -rf "$work_dir"
}
trap cleanup EXIT INT TERM

mkfifo "$bed_pipe"
echo "[1/4] Converting MVP rows to hg19 BED and running liftOver..." >&2
(
  gzip -dc "$source_file" | awk 'BEGIN { FS=OFS="\t" }
    NR == 1 { next }
    NF >= 9 && $2 ~ /^[0-9]+$/ {
      chrom=$1
      if (chrom == "23") chrom="X"
      else if (chrom == "24") chrom="Y"
      else if (chrom == "25" || chrom == "M") chrom="MT"
      if (chrom !~ /^chr/) chrom="chr" chrom
      print chrom, $2 - 1, $2, $3, $4, $5, $6, $7, $8, $9, $1, $2
    }'
) > "$bed_pipe" &
producer_pid=$!

if ! liftOver -bedPlus=3 -tab "$bed_pipe" "$chain_file" "$mapped_bed" "$unmapped_bed"; then
  wait "$producer_pid" || true
  exit 1
fi
wait "$producer_pid"

echo "[2/4] Sorting mapped hg38 variants..." >&2
LC_ALL=C sort --parallel="$threads" -S 2G -T "$work_dir" -k1,1V -k2,2n "$mapped_bed" > "$work_dir/mapped.sorted.hg38.bed"

echo "[3/4] Writing BGZF GWAS table..." >&2
{
  printf 'chromosome\tposition\trsid\tpvalue\tneg_log_pvalue\tref\talt\tbeta\tstderr_beta\talt_allele_freq\thg19_locus\n'
  awk 'BEGIN { FS=OFS="\t"; ln10=2.302585092994046 }
    NF >= 12 {
      p=exp(-($7 + 0) * ln10)
      printf "%s\t%d\t%s\t%.12g\t%s\t%s\t%s\t%s\t%s\t%s\t%s:%s\n", $1, $2 + 1, $4, p, $7, $5, $6, $8, $9, $10, $11, $12
    }' "$work_dir/mapped.sorted.hg38.bed"
} | "${bgzip_command[@]}" > "$temporary_output"

echo "[4/4] Building Tabix index..." >&2
tabix -f -S 1 -s 1 -b 2 -e 2 "$temporary_output"
mv "$temporary_output" "$output_file"
mv "${temporary_output}.tbi" "${output_file}.tbi"
chmod 664 "$output_file" "${output_file}.tbi"

mapped_count="$(wc -l < "$mapped_bed")"
unmapped_count="$(awk '!/^#/ && NF >= 3 { count += 1 } END { print count + 0 }' "$unmapped_bed")"
echo "Done: $output_file" >&2
echo "Mapped variants: $mapped_count; unmapped variants: $unmapped_count" >&2
trap - EXIT INT TERM
rm -rf "$work_dir"
