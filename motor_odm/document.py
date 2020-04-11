"""
This module contains the base class for interacting with Motor-ODM: :class:`Document`. The :class:`Document` class is
the main entry point to Motor-ODM and provides its main interface.
"""

from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Iterator,
    Optional,
    Sequence,
    Type,
    TypeVar,
    Union,
)

from bson import CodecOptions, ObjectId
from motor.core import AgnosticCollection, AgnosticDatabase
from pydantic import BaseModel, Field
from pydantic.main import ModelMetaclass
from pymongo import ReadPreference, ReturnDocument, WriteConcern
from pymongo.read_concern import ReadConcern

from motor_odm.helpers import monkey_patch

from .helpers import inherit_class

if TYPE_CHECKING:
    from pydantic.typing import (  # noqa: F401
        DictStrAny,
        AbstractSetIntStr,
        DictIntStrAny,
    )

    GenericDocument = TypeVar("GenericDocument", bound="Document")
    MongoType = Type["MongoBase"]

__all__ = ["DocumentMetaclass", "Document"]


@monkey_patch(ObjectId)
def __get_validators__() -> Iterator[Callable[[Any], ObjectId]]:
    """
    Returns a generator yielding the validators for the `ObjectId` type.
    """

    def validate(value: Union[str, bytes]) -> ObjectId:
        """
        Creates an `ObjectId` from a value. If the
        :param value: An `ObjectId` or a string.
        :return: An `ObjectId`.
        :raises TypeError: If `value` os not a string type.
        :raises InvalidId: If `value` does not represent a valid `ObjectId`.
        """

        return ObjectId(value)

    yield validate


class MongoBase:
    """This class defines the defaults for collection configurations.

    Each collection (defined by a subclass of :class:`Document`) can override these using an inner class named
    ``Mongo``. Attributes are implicitly and transitively inherited from the Mongo classes of base classes.
    """

    collection: Optional[str]
    """The name of the collection for a document. This attribute is required."""

    codec_options: Optional[CodecOptions] = None
    """The codec options to use when accessing the collection. Defaults to the database's :attr:`codec_options`."""

    read_preference: Optional[ReadPreference] = None
    """The read preference to use when accessing the collection. Defaults to the database's :attr:`read_preference`."""

    write_concern: Optional[WriteConcern] = None
    """The write concern to use when accessing the collection. Defaults to the database's :attr:`write_concern`."""

    read_concern: Optional[ReadConcern] = None
    """The read concern to use when accessing the collection. Defaults to the database's :attr:`read_concern`."""


class DocumentMetaclass(ModelMetaclass):
    """The meta class for :class:`Document`. Ensures that the ``Mongo`` class is automatically inherited."""

    def __new__(
        mcs, name: str, bases: Sequence[type], namespace: "DictStrAny", **kwargs: Any
    ) -> "DocumentMetaclass":
        mongo: MongoType = MongoBase
        for base in reversed(bases):
            if base != BaseModel and base != Document and issubclass(base, Document):
                # noinspection PyTypeChecker
                mongo = inherit_class("Mongo", base.__mongo__, mongo)
        # noinspection PyTypeChecker
        mongo = inherit_class("Mongo", namespace.get("Mongo"), mongo)

        if (namespace.get("__module__"), namespace.get("__qualname__")) != (
            "motor_odm.document",
            "Document",
        ):
            if not hasattr(mongo, "collection"):
                raise TypeError(f"{name} does not define a collection.")

        return super().__new__(mcs, name, bases, {"__mongo__": mongo, **namespace}, **kwargs)  # type: ignore


class Document(BaseModel, metaclass=DocumentMetaclass):
    """This is the base class for all documents defined using Motor-ODM.

    A :class:`Document` is a pydantic model that can be inserted into a MongoDB collection. This class provides an easy
    interface for interacting with the database. Each document has an :attr:`Document.id` (named ``_id`` in MongoDB) by
    default by which it can be uniquely identified in the database. The name of this field cannot be customized however
    you can override it if you don't want to use :class:`ObjectID <bson.objectid.ObjectId>` values for your IDs.
    """

    class Config:
        """:meta private:"""

        validate_all = True
        validate_assignment = True
        allow_population_by_field_name = True

    if TYPE_CHECKING:
        # populated by the metaclass, defined here to help IDEs only
        __mongo__: MongoType

    __db__: Optional[AgnosticDatabase]
    __collection__: Optional[AgnosticCollection] = None

    id: ObjectId = Field(None, alias="_id")
    """The document's ID in the database.

    By default this field is of type :class:`ObjectId <bson.objectid.ObjectId>` but it can be overridden to supply your
    own ID types. Note that if you intend to override this field you **must** set its alias to ``_id`` in order for your
    IDs to be recognized as such by MongoDB.
    """

    @classmethod
    def use(cls: Type["Document"], db: AgnosticDatabase) -> None:
        """Sets the database to be used by this :class:`Document`.

        The database will also be used by subclasses of this class unless they :meth:`use` their own database.

        This method has to be invoked before the ODM class can be used.
        """
        assert db is not None
        cls.__db__ = db

    @classmethod
    def db(cls) -> AgnosticDatabase:
        """Returns the database that is currently associated with this document.

        If no such database exists this returns the database of the parent document (its superclass). If no
        :class:`Document` class had its :meth:`use` method called to set a db, an :class:`AttributeError` is raised.
        """
        if not hasattr(cls, "__db__"):
            raise AttributeError("Accessing database without using it first.")
        return cls.__db__

    @classmethod
    def collection(cls: Type["Document"]) -> AgnosticCollection:
        """Returns the collection for this :class:`Document`.

        The collection uses the ``codec_options``, ``read_preference``, ``write_concern`` and ``read_concern`` from the
        document's ```Mongo``` class.
        """
        meta = cls.__mongo__
        if cls.__collection__ is None or cls.__collection__.database is not cls.db():
            cls.__collection__ = cls.db().get_collection(
                meta.collection,
                codec_options=meta.codec_options,
                read_preference=meta.read_preference,
                write_concern=meta.write_concern,
                read_concern=meta.read_concern,
            )
        return cls.__collection__

    def mongo(
        self,
        *,
        include: Union["AbstractSetIntStr", "DictIntStrAny"] = None,
        exclude: Union["AbstractSetIntStr", "DictIntStrAny"] = None,
    ) -> "DictStrAny":
        """Converts this object into a dictionary suitable to be saved to MongoDB."""
        document = self.dict(
            by_alias=True, include=include, exclude=exclude, exclude_defaults=True
        )
        if self.id is None:
            document.pop("_id", None)
        return document

    async def insert(self, *args: Any, **kwargs: Any) -> None:
        """Inserts the object into the database.

        The object is inserted as a new object.
        """
        result = await self.collection().insert_one(self.mongo(), *args, **kwargs)
        self.id = result.inserted_id

    @classmethod
    async def insert_many(
        cls: Type["GenericDocument"], *objects: "GenericDocument", **kwargs: Any
    ) -> None:
        """Inserts multiple documents at once.

        It is preferred to use this method over multiple :meth:`insert` calls as the performance can be much better.
        """
        result = await cls.collection().insert_many(
            [obj.mongo() for obj in objects], **kwargs
        )
        for obj, inserted_id in zip(objects, result.inserted_ids):
            obj.id = inserted_id

    async def replace(
        self: "GenericDocument", replace: "GenericDocument", *args: Any, **kwargs: Any,
    ) -> "GenericDocument":
        result = await self.collection().replace_one(
            self.mongo(), replace.mongo(), *args, **kwargs
        )
        if result.upserted_id:
            replace.id = result.upserted_id
        elif replace.id is None:
            replace.id = self.id
        return replace

    async def update(
        self, update: "DictStrAny", reload: bool = False, *args: Any, **kwargs: Any
    ) -> bool:
        assert self.id is not None
        result = await self.collection().update_one(
            {"_id": self.id}, update, *args, **kwargs
        )
        if reload:
            await self.reload()
        return result.modified_count == 1  # type: ignore

    async def reload(self, *args: Any, **kwargs: Any) -> None:
        """Reloads a document from the database.

        Use this method if a model might have changed in the database and you need to retrieve the current version. You
        do **not** need to call this after inserting a newly created object into the database.
        """
        assert self.id is not None
        updated = self.__class__(
            **await self.collection().find_one({"_id": self.id}, *args, **kwargs)
        )
        object.__setattr__(self, "__dict__", updated.__dict__)

    async def delete(self, *args: Any, **kwargs: Any) -> bool:
        result = await self.collection().delete_one(self.mongo(), *args, **kwargs)
        return result.deleted_count == 1  # type: ignore

    @classmethod
    async def delete_many(
        cls: Type["GenericDocument"], *objects: "GenericDocument"
    ) -> int:
        result = await cls.collection().delete_many([obj.mongo() for obj in objects])
        return result.deleted_count  # type: ignore

    @classmethod
    def find(
        cls: Type["GenericDocument"],
        filter: "DictStrAny" = None,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator["GenericDocument"]:
        async def context() -> AsyncIterator["GenericDocument"]:
            async for doc in cls.collection().find(filter, *args, **kwargs):
                yield cls(**doc)

        return context()

    @classmethod
    async def find_one(
        cls: Type["GenericDocument"],
        filter: "DictStrAny" = None,
        *args: Any,
        **kwargs: Any,
    ) -> Optional["GenericDocument"]:
        """Returns a single document from the collection."""
        doc = await cls.collection().find_one(filter, *args, **kwargs)
        return cls(**doc) if doc else None

    @classmethod
    async def find_one_and_delete(
        cls: Type["GenericDocument"],
        filter: "DictStrAny" = None,
        *args: Any,
        **kwargs: Any,
    ) -> Optional["GenericDocument"]:
        result = await cls.collection().find_one_and_delete(filter, *args, **kwargs)
        return cls(**result) if result else None

    @classmethod
    async def find_one_and_replace(
        cls: Type["GenericDocument"],
        filter: "DictStrAny",
        replacement: Union["DictStrAny", "GenericDocument"],
        return_document: bool = ReturnDocument.BEFORE,
        *args: Any,
        **kwargs: Any,
    ) -> Optional["GenericDocument"]:
        data = replacement.mongo() if isinstance(replacement, Document) else replacement
        result = await cls.collection().find_one_and_replace(
            filter, data, return_document=return_document, *args, **kwargs
        )
        if result is None:
            return None
        instance = cls(**result)
        if return_document == ReturnDocument.AFTER and isinstance(replacement, cls):
            object.__setattr__(replacement, "__dict__", instance.__dict__)
            return replacement
        else:
            return instance

    @classmethod
    async def find_one_and_update(
        cls: Type["GenericDocument"],
        filter: "DictStrAny",
        update: "DictStrAny",
        *args: Any,
        **kwargs: Any,
    ) -> Optional["GenericDocument"]:
        result = await cls.collection().find_one_and_update(
            filter, update, *args, **kwargs
        )
        return cls(**result) if result else None

    @classmethod
    async def count_documents(cls, *args: Any, **kwargs: Any) -> int:
        """Returns the number of documents in this class's collection.

        This method is filterable."""
        return await cls.collection().count_documents(*args, **kwargs)  # type: ignore
