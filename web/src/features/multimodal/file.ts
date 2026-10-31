export async function encodedFile(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32768)
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
  return { filename: file.name, media_type: file.type || "application/octet-stream", data_base64: btoa(chunks.join("")) };
}
