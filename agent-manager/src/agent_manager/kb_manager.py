import logging
from pathlib import Path
from typing import List

from .manager import Manager


class KnowledgeBaseManager(Manager):
    def __init__(self):
        self._knowledge_bases_path = Path("agent-manager/config/knowledge_bases")

    def create_knowledge_bases(self):
        """Create all knowledge bases by processing directories in knowledge_bases path"""
        if self._client is None:
            self.connect_to_llama_stack()

        logging.debug("Creating knowledge bases")

        if not self._knowledge_bases_path.exists():
            logging.warning(
                f"Knowledge bases path {self._knowledge_bases_path} does not exist"
            )
            return

        # Process each directory in knowledge_bases
        for kb_dir in self._knowledge_bases_path.iterdir():
            if kb_dir.is_dir():
                self.create_knowledge_base(kb_dir)

    def create_knowledge_base(self, kb_directory: Path):
        """Create a single knowledge base from a directory"""
        vector_db_id = kb_directory.name

        logging.info(f"Creating knowledge base: {vector_db_id}")

        try:
            # Get the first available vector_io provider
            providers = self._client.providers.list()
            vector_provider = next((p for p in providers if p.api == "vector_io"), None)

            if not vector_provider:
                logging.error("No vector_io provider found")
                return None

            # Register the vector database
            self._client.vector_dbs.register(
                vector_db_id=vector_db_id,
                provider_id=vector_provider.provider_id,
                embedding_model="all-MiniLM-L6-v2",
            )

            logging.info(f"Registered vector database: {vector_db_id}")

            # Insert documents from txt files in the directory
            self._insert_documents_from_directory(kb_directory, vector_db_id)

            return vector_db_id

        except Exception as e:
            logging.error(f"Failed to create knowledge base {vector_db_id}: {str(e)}")
            return None

    def _insert_documents_from_directory(self, directory: Path, vector_db_id: str):
        """Insert all txt files from a directory into the vector database"""
        rag_documents = []
        doc_counter = 0

        # Find all .txt files in the directory
        for file_path in directory.rglob("*.txt"):
            if file_path.is_file():
                doc_counter += 1
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    logging.debug(f"Processing file: {file_path}")

                    rag_documents.append(
                        {
                            "document_id": f"doc-{doc_counter}",
                            "content": content,
                            "mime_type": "text/plain",
                            "metadata": {
                                "source_file": str(file_path.name),
                                "kb_name": directory.name,
                            },
                        }
                    )

                except Exception as e:
                    logging.error(f"Failed to read file {file_path}: {str(e)}")
                    continue

        if rag_documents:
            try:
                logging.info(
                    f"Inserting {len(rag_documents)} documents into {vector_db_id}"
                )
                self._client.tool_runtime.rag_tool.insert(
                    documents=rag_documents,
                    vector_db_id=vector_db_id,
                    chunk_size_in_tokens=1000,
                )
                logging.info(f"Successfully inserted documents into {vector_db_id}")
            except Exception as e:
                logging.error(
                    f"Failed to insert documents into {vector_db_id}: {str(e)}"
                )
        else:
            logging.warning(f"No txt files found in {directory}")

    def delete_knowledge_bases(self):
        """Delete all registered vector databases"""
        if self._client is None:
            self.connect_to_llama_stack()

        logging.debug("Deleting knowledge bases")

        try:
            # Get list of vector databases (if available)
            # Note: This depends on the LlamaStack API having a list method
            vector_dbs = self._client.vector_dbs.list()

            for vector_db in vector_dbs:
                try:
                    self._client.vector_dbs.delete(vector_db.vector_db_id)
                    logging.info(f"Deleted vector database: {vector_db.vector_db_id}")
                except Exception as e:
                    logging.error(
                        f"Failed to delete vector database {vector_db.vector_db_id}: {str(e)}"
                    )

        except Exception as e:
            logging.error(f"Failed to list/delete vector databases: {str(e)}")

    def list_knowledge_bases(self) -> List[str]:
        """List all available knowledge base directories"""
        knowledge_bases = []

        if not self._knowledge_bases_path.exists():
            return knowledge_bases

        for kb_dir in self._knowledge_bases_path.iterdir():
            if kb_dir.is_dir():
                knowledge_bases.append(kb_dir.name)

        return knowledge_bases

    def get_vector_db_ids(self) -> List[str]:
        """Get vector database IDs for all knowledge bases"""
        kb_names = self.list_knowledge_bases()
        return [kb_name for kb_name in kb_names]
