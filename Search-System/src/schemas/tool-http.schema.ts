import {Ajv} from "ajv";
import {objectSchema,nonBlankText,nullable,scalar} from "./contract-schema.helpers.js";
import {productFactsSchema} from "./search-response.schema.js";
import {TOOL_AJV_OPTIONS} from "../validation/tool-search.validation.js";
import type {CatalogProductDetails} from "../types/catalog-read.types.js";
export const httpErrorSchema=objectSchema({version:{const:"1"},request_id:nonBlankText(100),
 status:{enum:["unauthorized","not_found","invalid_request","search_unavailable"]},
 issues:{type:"array",minItems:1,items:objectSchema({path:{type:"string"},code:nonBlankText(),message:nonBlankText(1000)})}});
export const detailsResponseSchema=objectSchema({version:{const:"1"},request_id:nonBlankText(100),status:{const:"ok"},
 details:objectSchema({
  product:productFactsSchema,name_en:nullable({type:"string"}),series:nullable({type:"string"}),short_description:{type:"string"},
  regular_price:nullable({type:"number",minimum:0}),sale_unit:nonBlankText(),pack_quantity:{type:"integer",minimum:1},stock_quantity:{type:"integer",minimum:0},
  specifications:{type:"array",items:objectSchema({attribute_id:{type:"string",pattern:"^AT[0-9]{4}$"},name:nonBlankText(),value:nullable(scalar),unit:nullable(nonBlankText(32)),
   data_type:{enum:["number","text","boolean"]},numeric_decimal:nullable({type:"string"}),is_required:{type:"boolean"}})},
  feature_details:{type:"array",items:objectSchema({feature_id:{type:"string",pattern:"^FT[0-9]{4}$"},name:nonBlankText(),detail:{type:"string"},benefit:{type:"string"},limitation:{type:"string"}})},
  content_sections:{type:"array",items:objectSchema({section_type:nonBlankText(),title:nonBlankText(),applicability:nonBlankText(),items:{},display_order:{type:"integer"}})},
 })});
const validate=new Ajv(TOOL_AJV_OPTIONS).compile(detailsResponseSchema);
export function validDetailsResponse(input:unknown):input is {version:"1";request_id:string;status:"ok";details:CatalogProductDetails} {
 if(!validate(input))return false;
 const d=(input as {details:CatalogProductDetails}).details;
 return (!d.product.is_mock || Boolean(d.product.mock_notice?.trim())) && d.specifications.every(s=>s.value===null ||
   typeof s.value===(s.data_type==="number"?"number":s.data_type==="boolean"?"boolean":"string"));
}
